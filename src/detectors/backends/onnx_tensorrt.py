"""
ONNX TensorRT 检测器后端

使用 ONNX Runtime TensorrtExecutionProvider 进行推理。
适用于：
- NVIDIA GPU 高性能推理
- 需要自动 ONNX 到 TensorRT 转换的场景
- 生产环境中的极致性能需求

特性：
- 自动 ONNX 到 TensorRT Engine 转换
- Engine 缓存机制（避免重复编译）
- FP16 精度加速
- CUDA 回退支持（如果 TensorRT 不可用）
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


def _setup_tensorrt_lib_path(config_tensorrt_lib_path: str = "") -> str:
    """
    设置 TensorRT lib 路径
    
    优先级：
    1. 配置文件中的 tensorrt_lib_path
    2. 环境变量 TENSORRT_LIB_PATH
    3. 自动检测常见路径
    
    Args:
        config_tensorrt_lib_path: 配置文件中的 TensorRT lib 路径
    
    Returns:
        TensorRT lib 路径
    """
    tensorrt_lib = ""
    
    # 1. 使用配置文件中的路径（最高优先级）
    if config_tensorrt_lib_path and os.path.exists(config_tensorrt_lib_path):
        tensorrt_lib = config_tensorrt_lib_path
        logger.debug(f"[ONNXTensorRTDetector] 使用配置文件中的 TensorRT 路径: {tensorrt_lib}")
    
    # 2. 使用环境变量
    if not tensorrt_lib:
        tensorrt_lib = os.environ.get("TENSORRT_LIB_PATH", "")
        if tensorrt_lib:
            logger.debug(f"[ONNXTensorRTDetector] 使用环境变量 TENSORRT_LIB_PATH: {tensorrt_lib}")
    
    # 3. 自动检测常见路径（Windows）
    if not tensorrt_lib and os.name == 'nt':  # Windows
        default_paths = [
            "D:\\TensorRT\\lib",
            "C:\\Program Files\\TensorRT\\lib",
        ]
        
        # 尝试自动检测（查找包含 nvinfer.dll 的目录）
        for path in default_paths:
            if os.path.exists(path):
                tensorrt_lib = path
                logger.debug(f"[ONNXTensorRTDetector] 自动检测到 TensorRT 路径: {path}")
                break
    
    # 添加到系统 PATH
    if tensorrt_lib and tensorrt_lib not in os.environ.get("PATH", ""):
        if os.name == 'nt':  # Windows
            os.environ["PATH"] = tensorrt_lib + ";" + os.environ.get("PATH", "")
        else:  # Linux
            os.environ["PATH"] = tensorrt_lib + ":" + os.environ.get("PATH", "")
        logger.debug(f"[ONNXTensorRTDetector] TensorRT lib 路径已添加到 PATH")
    
    return tensorrt_lib


class ONNXTensorRTDetector(BaseDetector):
    """
    ONNX TensorRT 检测器
    
    使用 ONNX Runtime 的 TensorrtExecutionProvider 进行推理。
    特性：
    - 自动将 ONNX 模型转换为 TensorRT Engine
    - Engine 缓存机制（避免重复编译）
    - FP16 精度加速
    
    首次运行时会编译 TensorRT Engine（可能耗时 2-5 分钟），
    后续运行会直接加载缓存的 Engine。
    
    推荐配置：
    - GPU: NVIDIA RTX 2070 或更高
    - TensorRT: 10.x
    - CUDA: 12.x
    """
    
    def __init__(self, config: DetectorConfig):
        """
        初始化 ONNX TensorRT 检测器
        
        Args:
            config: 检测器配置对象
        """
        super().__init__(config)
        
        # ONNX Runtime 会话
        self.session: Optional[ort.InferenceSession] = None
        
        # TensorRT 配置
        self._device_id = config.device_id
        self._use_fp16 = config.use_fp16
        self._engine_cache_path = config.tensorrt_engine_cache_path
        self._tensorrt_lib_path = config.tensorrt_lib_path
        
        logger.debug(f"[ONNXTensorRTDetector] 初始化完成")
    
    def initialize(self):
        """
        初始化检测器（加载模型）
        
        此方法在创建对象后调用，用于延迟加载模型。
        """
        try:
            logger.debug(f"[ONNXTensorRTDetector] 正在初始化检测器...")
            logger.debug(f"[ONNXTensorRTDetector]   模型路径: {self.config.model_path}")
            logger.debug(f"[ONNXTensorRTDetector]   Engine缓存路径: {self._engine_cache_path}")
            logger.debug(f"[ONNXTensorRTDetector]   FP16模式: {self._use_fp16}")
            sys.stdout.flush()
            
            self.load_model()
            
            logger.debug(f"[ONNXTensorRTDetector] 检测器初始化完成")
            sys.stdout.flush()
        except Exception as e:
            logger.debug(f"[ONNXTensorRTDetector] 检测器初始化失败: {e}")
            sys.stdout.flush()
            raise
    
    def load_model(self, model_path: str = None):
        """
        加载 ONNX 模型（使用 TensorRT Execution Provider）
        
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
            # 设置 TensorRT lib 路径
            # ========================================
            _setup_tensorrt_lib_path(self._tensorrt_lib_path)
            
            # ========================================
            # 创建会话选项
            # ========================================
            session_options = ort.SessionOptions()
            session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            
            # 启用 TensorRT 详细日志（便于调试）
            os.environ['ORT_TENSORRT_LOGGER_LEVEL'] = '1'  # 0=OFF, 1=INFO, 2=VERBOSE
            
            # ========================================
            # TensorRT Execution Provider 配置
            # ========================================
            trt_ep_options = {
                'device_id': self._device_id,
                
                # 启用 FP16 精度
                'trt_fp16_enable': self._use_fp16,
                
                # 启用 Engine 缓存（避免重复编译）
                'trt_engine_cache_enable': True,
                
                # Engine 缓存路径
                'trt_engine_cache_path': self._engine_cache_path,
            }
            
            # Provider 列表（TensorRT优先，CUDA和CPU作为回退）
            providers = [
                ('TensorrtExecutionProvider', trt_ep_options),
                ('CUDAExecutionProvider', {'device_id': self._device_id}),
                'CPUExecutionProvider'
            ]
            
            # ========================================
            # 创建推理会话
            # ========================================
            logger.debug(f"[ONNXTensorRTDetector] 正在加载模型并创建推理会话...")
            logger.debug(f"[ONNXTensorRTDetector]   检查 Engine 缓存: {self._engine_cache_path}")
            logger.debug(f"[ONNXTensorRTDetector]   如果缓存不存在，TensorRT 将编译新 Engine（可能耗时 2-5 分钟）")
            logger.debug(f"[ONNXTensorRTDetector]   请观察控制台是否有 TensorRT 编译日志输出...")
            sys.stdout.flush()
            
            self.session = ort.InferenceSession(
                model_path,
                sess_options=session_options,
                providers=providers
            )
            
            logger.debug(f"[ONNXTensorRTDetector] 推理会话创建完成！")
            sys.stdout.flush()
            
            # 获取输入信息
            inputs = self.session.get_inputs()
            self.input_name = inputs[0].name
            self.input_shape = tuple(inputs[0].shape)
            
            # 获取实际使用的 Provider
            active_providers = self.session.get_providers()
            
            # 打印加载信息
            logger.debug(f"[ONNXTensorRTDetector] ONNX 模型加载成功（TensorRT 后端）：{model_path}")
            logger.debug(f"  输入名称：{self.input_name}")
            logger.debug(f"  输入形状：{self.input_shape}")
            logger.debug(f"  可用 Provider: {active_providers}")
            logger.debug(f"  FP16 加速: {self._use_fp16}")
            logger.debug(f"  Engine 缓存: {self._engine_cache_path}")
            sys.stdout.flush()
            
            # 验证 TensorRT Provider 是否生效
            if 'TensorrtExecutionProvider' not in active_providers:
                logger.debug(f"[ONNXTensorRTDetector] [警告] TensorRT EP 未激活！实际使用：{active_providers}")
                logger.debug(f"[ONNXTensorRTDetector] 可能原因：")
                logger.debug(f"    1. 首次运行正在编译 Engine（可能需要 2-5 分钟）")
                logger.debug(f"    2. TensorRT 版本与 CUDA 版本不匹配")
                logger.debug(f"    3. ONNX 模型包含 TensorRT 不支持的算子")
                logger.debug(f"    4. 缺少 TensorRT 动态库")
            else:
                logger.debug(f"[ONNXTensorRTDetector] [OK] TensorRT EP 已确认激活")
            
        except Exception as e:
            logger.debug(f"[ONNXTensorRTDetector] TensorRT 加载失败：{e}")
            raise RuntimeError(f"模型加载失败：{e}")
    
    def infer(self, input_tensor: np.ndarray) -> List[Detection]:
        """
        执行推理（TensorRT）
        
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
            logger.debug(f"[ONNXTensorRTDetector] 推理失败：{e}")
            logger.debug(f"[ONNXTensorRTDetector] 错误详情:\n{error_details}")
            raise RuntimeError(f"推理失败：{e}")
    
    def unload_model(self):
        """
        卸载模型
        
        释放ONNX Runtime会话和资源。
        """
        if self.session is not None:
            del self.session
            self.session = None
            self.input_name = None
            self.input_shape = None
        
        logger.debug("[ONNXTensorRTDetector] 模型已卸载")
    
    def get_info(self) -> Dict:
        """
        获取检测器信息
        
        Returns:
            检测器信息字典
        """
        info = {
            'backend': 'onnx_tensorrt',
            'model_path': self.config.model_path,
            'input_shape': self.input_shape,
            'device_id': self._device_id,
            'use_fp16': self._use_fp16,
            'engine_cache_path': self._engine_cache_path,
            'class_names': self.config.class_names,
            'conf_threshold': self.config.conf_threshold,
            'iou_threshold': self.config.iou_threshold
        }
        
        # 添加推理统计信息
        stats = self.get_inference_stats()
        info.update(stats)
        
        return info