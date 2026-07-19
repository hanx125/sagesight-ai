"""
ONNX CPU 检测器后端

使用 ONNX Runtime CPUExecutionProvider 进行推理。
适用于：
- 无GPU环境的推理
- 调试和测试
- 资源受限的边缘设备（非NPU）

特性：
- 纯CPU推理，无需GPU依赖
- 线程数优化，避免多任务时CPU占用过高
- 自动图优化
"""

import os
import sys
import time
import multiprocessing
from typing import List, Dict, Optional, Tuple

import numpy as np
import onnxruntime as ort

from src.detectors.base import BaseDetector, Detection, DetectorConfig


class ONNXCPUDetector(BaseDetector):
    """
    ONNX CPU 检测器
    
    使用 ONNX Runtime 的 CPUExecutionProvider 进行推理。
    针对CPU推理进行了优化：
    - 限制线程数，避免多任务时CPU占用过高
    - 启用图优化
    - 适合无GPU环境或调试场景
    
    注意：CPU推理速度较慢，建议在生产环境中使用GPU后端。
    """
    
    def __init__(self, config: DetectorConfig):
        """
        初始化 ONNX CPU 检测器
        
        Args:
            config: 检测器配置对象
        """
        super().__init__(config)
        
        # ONNX Runtime 会话
        self.session: Optional[ort.InferenceSession] = None
        
        # CPU线程数配置
        self._num_threads = 0  # 将在load_model时计算
        
        print(f"[ONNXCPUDetector] 初始化完成")
    
    def initialize(self):
        """
        初始化检测器（加载模型）
        
        此方法在创建对象后调用，用于延迟加载模型。
        """
        try:
            print(f"[ONNXCPUDetector] 正在初始化检测器...")
            print(f"[ONNXCPUDetector]   模型路径: {self.config.model_path}")
            sys.stdout.flush()
            
            self.load_model()
            
            print(f"[ONNXCPUDetector] 检测器初始化完成")
            sys.stdout.flush()
        except Exception as e:
            print(f"[ONNXCPUDetector] 检测器初始化失败: {e}")
            sys.stdout.flush()
            raise
    
    def load_model(self, model_path: str = None):
        """
        加载 ONNX 模型（使用 CPU）
        
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
            # 创建会话选项（CPU优化）
            # ========================================
            session_options = ort.SessionOptions()
            
            # 启用图优化
            session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            
            # ========================================
            # CPU线程数优化
            # ========================================
            # 限制线程数，避免多任务时CPU占用过高
            # 建议：每个任务使用2-4个线程，而不是所有核心
            num_cores = multiprocessing.cpu_count()
            
            # 限制每个任务的线程数
            # - 如果总核心数 < 4，使用实际核心数的一半
            # - 如果总核心数 >= 4，最多使用4个线程
            max_threads_per_task = min(4, num_cores // 2 if num_cores > 2 else 1)
            self._num_threads = max_threads_per_task
            
            session_options.intra_op_num_threads = max_threads_per_task
            session_options.inter_op_num_threads = max_threads_per_task
            
            print(f"[ONNXCPUDetector] CPU线程数限制: {max_threads_per_task} (总核心数: {num_cores})")
            
            # ========================================
            # 创建推理会话
            # ========================================
            providers = ['CPUExecutionProvider']
            
            self.session = ort.InferenceSession(
                model_path,
                sess_options=session_options,
                providers=providers
            )
            
            # 获取输入信息
            inputs = self.session.get_inputs()
            self.input_name = inputs[0].name
            self.input_shape = tuple(inputs[0].shape)
            
            print(f"[ONNXCPUDetector] ONNX 模型加载成功（CPU 后端）：{model_path}")
            print(f"  输入名称：{self.input_name}")
            print(f"  输入形状：{self.input_shape}")
            print(f"  CPU 核心数：{num_cores}")
            print(f"  使用线程数：{self._num_threads}")
            print(f"  提示：CPU推理速度较慢，建议使用GPU后端")
            
        except Exception as e:
            raise RuntimeError(f"模型加载失败：{e}")
    
    def infer(self, input_tensor: np.ndarray) -> List[Detection]:
        """
        执行推理（CPU）
        
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
            print(f"[ONNXCPUDetector] 推理失败：{e}")
            print(f"[ONNXCPUDetector] 错误详情:\n{error_details}")
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
        
        print("[ONNXCPUDetector] 模型已卸载")
    
    def get_info(self) -> Dict:
        """
        获取检测器信息
        
        Returns:
            检测器信息字典
        """
        info = {
            'backend': 'onnx_cpu',
            'model_path': self.config.model_path,
            'input_shape': self.input_shape,
            'num_threads': self._num_threads,
            'class_names': self.config.class_names,
            'conf_threshold': self.config.conf_threshold,
            'iou_threshold': self.config.iou_threshold
        }
        
        # 添加推理统计信息
        stats = self.get_inference_stats()
        info.update(stats)
        
        return info