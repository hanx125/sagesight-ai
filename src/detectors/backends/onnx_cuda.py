"""
ONNX CUDA 检测器后端

使用 ONNX Runtime CUDAExecutionProvider 进行推理。
适用于：
- NVIDIA GPU 加速推理
- CUDA 兼容的深度学习模型
- 生产环境中的高性能推理

特性：
- GPU 加速推理（CUDA）
- GPU 内存限制配置（防止显存溢出）
- CUDA Graph 加速（需要 ONNX Runtime 1.17+）
- cuDNN 算法优化
"""

import os
import sys
import time
from typing import List, Dict, Optional, Tuple

import numpy as np
import onnxruntime as ort

from src.detectors.base import BaseDetector, Detection, DetectorConfig
import logging

logger = logging.getLogger(__name__)


class ONNXCUDADetector(BaseDetector):
    """
    ONNX CUDA 检测器
    
    使用 ONNX Runtime 的 CUDAExecutionProvider 进行推理。
    针对 NVIDIA GPU 进行了优化：
    - 配置 GPU 内存限制（防止显存溢出）
    - 启用 CUDA Graph 加速（如果支持）
    - cuDNN 卷积算法优化
    
    推荐配置：
    - GPU: NVIDIA RTX 2070 或更高
    - CUDA: 11.x 或 12.x
    - cuDNN: 8.x+
    """
    
    def __init__(self, config: DetectorConfig):
        """
        初始化 ONNX CUDA 检测器
        
        Args:
            config: 检测器配置对象
        """
        super().__init__(config)
        
        # ONNX Runtime 会话
        self.session: Optional[ort.InferenceSession] = None
        
        # GPU 配置
        self._device_id = config.device_id
        self._gpu_mem_limit = config.gpu_mem_limit
        
        logger.debug(f"[ONNXCUDADetector] 初始化完成")
    
    def initialize(self):
        """
        初始化检测器（加载模型）
        
        此方法在创建对象后调用，用于延迟加载模型。
        """
        try:
            logger.debug(f"[ONNXCUDADetector] 正在初始化检测器...")
            logger.debug(f"[ONNXCUDADetector]   模型路径: {self.config.model_path}")
            logger.debug(f"[ONNXCUDADetector]   GPU设备ID: {self._device_id}")
            sys.stdout.flush()
            
            self.load_model()
            
            logger.debug(f"[ONNXCUDADetector] 检测器初始化完成")
            sys.stdout.flush()
        except Exception as e:
            logger.debug(f"[ONNXCUDADetector] 检测器初始化失败: {e}")
            sys.stdout.flush()
            raise
    
    def load_model(self, model_path: str = None):
        """
        加载 ONNX 模型（使用 CUDA）
        
        Args:
            model_path: 模型路径（可选，覆盖配置中的路径）
        
        Raises:
            FileNotFoundError: 模型文件不存在
            ValueError: 模型文件格式错误
            RuntimeError: 模型加载失败
        """
        # 使用配置中的模型路径或参数指定的路径
        model_path = model_path or self.config.model_path
        
        if not model_path:
            raise FileNotFoundError("模型路径未指定")
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"模型文件不存在：{model_path}")
        
        if not model_path.endswith('.onnx'):
            raise ValueError(f"模型文件必须是 ONNX 格式：{model_path}")
        
        try:
            # ========================================
            # 创建会话选项（针对 GPU 优化）
            # ========================================
            session_options = ort.SessionOptions()

            # CUDA Graph 在多进程共享 GPU 场景下不安全：
            # - 多个检测器进程在同一 GPU 上各自捕获/重放 CUDA Graph
            # - 捕获的图引用 cuDNN handle，被其他进程的 CUDA 操作破坏
            # - 导致 cudaErrorInvalidResourceHandle
            # 仅在单进程场景下可手动启用
            # 旧版 onnxruntime 无此属性，用 try-except 兼容
            try:
                session_options.enable_cuda_graph = False
            except AttributeError:
                pass

            # 启用图优化
            session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            # ========================================
            # CUDA Execution Provider 配置
            # ========================================
            cuda_options = {
                'device_id': self._device_id,
                # cuDNN 卷积算法搜索策略
                # 多进程共享 GPU 时必须用 'DEFAULT'：
                # - 'HEURISTIC'/'EXHAUSTIVE' 每个进程独立搜索，分配额外显存做 benchmark
                # - 多进程叠加导致显存压力过大 → CUDNN_STATUS_EXECUTION_FAILED
                'cudnn_conv_algo_search': 'DEFAULT',

                # 内存分配策略：按需分配，节省显存
                'arena_extend_strategy': 'kSameAsRequested',
            }

            # GPU 内存限制：多进程场景下不设置 per-process gpu_mem_limit
            # gpu_mem_limit 是每进程上限，N 个进程 × 6GB 在 8GB 显卡上会互相抢占
            # 不传该键时 ONNX 动态管理显存，按需分配，避免多进程冲突
            # 单进程场景如需限制，可在配置中设置（>= 1GB 才生效）
            if self._gpu_mem_limit and self._gpu_mem_limit >= 1024 * 1024 * 1024:
                cuda_options['gpu_mem_limit'] = self._gpu_mem_limit

            # Provider 列表（CUDA优先，CPU作为回退）
            providers = [
                ('CUDAExecutionProvider', cuda_options),
                'CPUExecutionProvider'  # 回退选项
            ]
            
            # ========================================
            # 创建推理会话
            # ========================================
            # 注册 CUDA/cuDNN 运行库目录：缺少 cuDNN 时卷积等算子会退化为兜底实现
            from src.utils.platform_utils import register_cuda_dll_dirs
            register_cuda_dll_dirs()

            self.session = ort.InferenceSession(
                model_path,
                sess_options=session_options,
                providers=providers
            )
            
            # 获取输入信息
            inputs = self.session.get_inputs()
            self.input_name = inputs[0].name
            self.input_shape = tuple(inputs[0].shape)
            
            # 获取实际使用的 Provider
            active_providers = self.session.get_providers()
            
            # 打印加载信息
            logger.debug(f"[ONNXCUDADetector] ONNX 模型加载成功（CUDA 后端）：{model_path}")
            logger.debug(f"  输入名称：{self.input_name}")
            logger.debug(f"  输入形状：{self.input_shape}")
            logger.debug(f"  可用 Provider: {active_providers}")
            logger.debug(f"  GPU 设备ID: {self._device_id}")
            if self._gpu_mem_limit and self._gpu_mem_limit >= 1024 * 1024 * 1024:
                logger.debug(f"  GPU 内存限制: {self._gpu_mem_limit / (1024**3):.1f} GB (per-process)")
            else:
                logger.debug(f"  GPU 内存限制: 不限制（ONNX 动态管理，适用于多进程）")
            
            # 验证 CUDA Provider 是否生效
            if 'CUDAExecutionProvider' not in active_providers:
                logger.warning(f"[ONNXCUDADetector] CUDA EP 未激活，推理已回退到 CPU！实际使用：{active_providers}")
                logger.warning(f"[ONNXCUDADetector] 可能原因：")
                logger.warning(f"    1. CUDA 未正确安装")
                logger.warning(f"    2. ONNX Runtime GPU 版本未安装")
                logger.warning(f"    3. GPU 驱动版本不兼容")
            else:
                logger.debug(f"[ONNXCUDADetector] [OK] CUDA EP 已确认激活")
            
        except Exception as e:
            raise RuntimeError(f"模型加载失败：{e}")
    
    def infer(self, input_tensor: np.ndarray) -> List[Detection]:
        """
        执行推理（CUDA）
        
        Args:
            input_tensor: 预处理后的张量 (1, 3, H, W) float32，范围 [0, 1]
        
        Returns:
            检测结果列表
        
        Raises:
            RuntimeError: 模型未加载或推理失败
        """
        if self.session is None:
            raise RuntimeError("ONNX 模型未加载，请先调用 load_model()")
        
        try:
            start_time = time.time()
            
            # ========================================
            # 执行ONNX推理
            # ========================================
            outputs = self.session.run(None, {self.input_name: input_tensor})
            
            # 健壮性检查
            if not outputs or len(outputs) == 0:
                raise RuntimeError("推理返回空输出")
            
            output = outputs[0]
            
            # ========================================
            # 后处理（使用基类方法）
            # ========================================
            img_height = input_tensor.shape[2]
            img_width = input_tensor.shape[3]
            
            detections = self._postprocess_yolo(
                output=output,
                img_height=img_height,
                img_width=img_width,
                conf_threshold=self.config.conf_threshold,
                iou_threshold=self.config.iou_threshold,
                class_names=self.config.class_names,
                min_box_area=self.config.min_box_area
            )
            
            # 更新统计信息
            inference_time = time.time() - start_time
            self._update_stats(inference_time)
            
            return detections
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.debug(f"[ONNXCUDADetector] 推理失败：{e}")
            logger.debug(f"[ONNXCUDADetector] 错误详情:\n{error_details}")
            raise RuntimeError(f"推理失败：{e}")
    
    def unload_model(self):
        """
        卸载模型
        
        释放ONNX Runtime会话和GPU资源。
        """
        if self.session is not None:
            del self.session
            self.session = None
            self.input_name = None
            self.input_shape = None
        
        logger.debug("[ONNXCUDADetector] 模型已卸载")
    
    def get_info(self) -> Dict:
        """
        获取检测器信息
        
        Returns:
            检测器信息字典
        """
        info = {
            'backend': 'onnx_cuda',
            'model_path': self.config.model_path,
            'input_shape': self.input_shape,
            'device_id': self._device_id,
            'gpu_mem_limit_gb': self._gpu_mem_limit / (1024**3) if self._gpu_mem_limit else 0,
            'class_names': self.config.class_names,
            'conf_threshold': self.config.conf_threshold,
            'iou_threshold': self.config.iou_threshold
        }
        
        # 添加推理统计信息
        stats = self.get_inference_stats()
        info.update(stats)
        
        return info