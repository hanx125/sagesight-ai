"""
RKNN 检测器后端

使用瑞芯微 NPU 进行推理。
适用于：
- RK3588 / RK3566 / RK3568 / RV1103 / RV1106 等瑞芯微平台
- 边缘设备推理（NPU 加速）
- 低功耗、高性能推理

特性：
- NPU 加速推理（比 CPU 快 10-20 倍）
- 支持多核 NPU（RK3588 双核 NPU）
- 支持 NCHW/NHWC 格式自动转换
- 低功耗运行

注意：
- 需要 RKNN 模型文件（.rknn）
- 需要 rknn-toolkit2 Python 包
- 仅在瑞芯微平台上可用
"""

import os
import sys
import time
from typing import List, Dict, Optional, Tuple

import numpy as np

from src.detectors.base import BaseDetector, Detection, DetectorConfig

# RKNN Executor 导入（仅瑞芯微平台可用）
RKNN_EXECUTOR_AVAILABLE = False
try:
    from src.utils.rknn_executor import RKNNExecutor, is_rknn_available
    RKNN_EXECUTOR_AVAILABLE = True
    print(f"[RKNNDetector] RKNN Executor 已加载")
except ImportError:
    print(f"[RKNNDetector] RKNN Executor 未安装（仅瑞芯微平台可用）")


class RKNNDetector(BaseDetector):
    """
    RKNN 检测器
    
    使用瑞芯微 NPU 进行推理。
    
    支持的平台：
    - RK3588：双核 NPU，6 TOPS 算力
    - RK3566：单核 NPU，1 TOPS 算力
    - RK3568：单核 NPU，1 TOPS 算力
    - RV1103：单核 NPU，0.5 TOPS 算力
    - RV1106：单核 NPU，0.5 TOPS 算力
    
    使用方式：
    1. 在 PC 上使用 rknn-toolkit2 转换 ONNX 模型为 RKNN 模型
    2. 在瑞芯微平台上加载 RKNN 模型进行推理
    
    注意：
    - RKNN 模型输入格式通常为 NHWC（与 ONNX 的 NCHW 不同）
    - 此检测器会自动进行格式转换
    """
    
    def __init__(self, config: DetectorConfig):
        """
        初始化 RKNN 检测器
        
        Args:
            config: 检测器配置对象
        """
        super().__init__(config)
        
        # RKNN Executor
        self._rknn_executor = None
        
        # RKNN 配置
        self._rknn_target = config.rknn_target
        self._rknn_device_id = config.rknn_device_id
        self._rknn_core_mask = config.rknn_core_mask
        
        print(f"[RKNNDetector] 初始化完成")
        print(f"[RKNNDetector]   目标平台: {self._rknn_target}")
    
    def initialize(self):
        """
        初始化检测器（加载模型）
        """
        try:
            print(f"[RKNNDetector] 正在初始化检测器...")
            print(f"[RKNNDetector]   模型路径: {self.config.model_path}")
            print(f"[RKNNDetector]   目标平台: {self._rknn_target}")
            sys.stdout.flush()
            
            self.load_model()
            
            print(f"[RKNNDetector] 检测器初始化完成")
            sys.stdout.flush()
        except Exception as e:
            print(f"[RKNNDetector] 检测器初始化失败: {e}")
            sys.stdout.flush()
            raise
    
    def load_model(self, model_path: str = None):
        """
        加载 RKNN 模型
        
        Args:
            model_path: 模型路径（可选，覆盖配置中的路径）
        
        Raises:
            ImportError: RKNN Executor 未安装
            FileNotFoundError: 模型文件不存在
            ValueError: 模型文件格式错误
            RuntimeError: 模型加载失败
        """
        # 检查 RKNN Executor 是否可用
        if not RKNN_EXECUTOR_AVAILABLE:
            raise ImportError(
                "RKNN Executor 未安装，无法使用 RKNN 后端。\n\n"
                "请在瑞芯微平台上安装 rknn-toolkit2:\n"
                "pip install rknn-toolkit2\n\n"
                "或在 PC 上安装 rknn-toolkit2-lite 进行转换:\n"
                "pip install rknn-toolkit2-lite"
            )
        
        # 使用配置中的模型路径或参数指定的路径
        model_path = model_path or self.config.model_path
        
        if not model_path:
            raise FileNotFoundError("RKNN 模型路径未指定")
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"RKNN 模型文件不存在：{model_path}")
        
        if not model_path.endswith('.rknn'):
            raise ValueError(f"模型文件必须是 RKNN 格式：{model_path}")
        
        try:
            # ========================================
            # 创建 RKNN Executor
            # ========================================
            self._rknn_executor = RKNNExecutor(
                model_path=model_path,
                target=self._rknn_target,
                device_id=self._rknn_device_id,
                core_mask=self._rknn_core_mask
            )
            
            # ========================================
            # RKNN 输入格式说明
            # ========================================
            # RKNN 模型输入通常是 NHWC 格式（与 ONNX 的 NCHW 不同）
            # input_shape 在这里设置为 NCHW 格式（统一接口）
            # 在 infer() 中会自动转换为 NHWC
            
            # 假设 RKNN 模型输入尺寸为 640x640（需根据实际模型调整）
            self.input_shape = (1, 3, self.config.input_height, self.config.input_width)
            
            # 打印加载信息
            print(f"[RKNNDetector] RKNN 模型加载成功：{model_path}")
            print(f"  目标平台：{self._rknn_target}")
            print(f"  输入形状（NCHW）：{self.input_shape}")
            print(f"  输入形状（NHWC）：{(1, self.config.input_height, self.config.input_width, 3)}")
            print(f"  提示：RKNN 推理使用 NPU 加速")
            
        except Exception as e:
            raise RuntimeError(f"RKNN 模型加载失败：{e}")
    
    def infer(self, input_tensor: np.ndarray) -> List[Detection]:
        """
        执行推理（RKNN NPU）
        
        自动将 NCHW 格式转换为 NHWC 格式。
        
        Args:
            input_tensor: 预处理后的张量 (1, 3, H, W) float32，范围 [0, 1]
                          注意：输入是 NCHW 格式，会自动转换为 NHWC
        
        Returns:
            检测结果列表
        
        Raises:
            RuntimeError: 模型未加载或推理失败
        """
        if self._rknn_executor is None:
            raise RuntimeError("RKNN 模型未加载，请先调用 load_model()")
        
        try:
            start_time = time.time()
            
            # ========================================
            # 格式转换：NCHW → NHWC
            # ========================================
            # ONNX 输入：(1, 3, H, W) NCHW 格式
            # RKNN 输入：(1, H, W, 3) NHWC 格式
            if input_tensor.ndim == 4 and input_tensor.shape[1] == 3:
                # NCHW → NHWC
                input_tensor_nhwc = np.transpose(input_tensor, (0, 2, 3, 1))
            elif input_tensor.ndim == 4 and input_tensor.shape[3] == 3:
                # 已经是 NHWC 格式，直接使用
                input_tensor_nhwc = input_tensor
            else:
                raise ValueError(
                    f"输入张量格式错误：期望 (1, 3, H, W) NCHW 或 (1, H, W, 3) NHWC，"
                    f"得到 {input_tensor.shape}"
                )
            
            # ========================================
            # 执行 RKNN 推理
            # ========================================
            outputs = self._rknn_executor.run(input_tensor_nhwc)
            
            # ========================================
            # 输出格式处理
            # ========================================
            # RKNN 输出可能是：
            # 1. 单个 ndarray
            # 2. 多个 ndarray 的列表（boxes, classes, scores）
            
            if isinstance(outputs, (list, tuple)):
                if len(outputs) > 0:
                    output = outputs[0]
                else:
                    raise RuntimeError("RKNN 推理返回空输出")
            else:
                output = outputs
            
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
            print(f"[RKNNDetector] 推理失败：{e}")
            print(f"[RKNNDetector] 错误详情:\n{error_details}")
            raise RuntimeError(f"推理失败：{e}")
    
    def unload_model(self):
        """
        卸载模型
        
        释放 RKNN Executor 和 NPU 资源。
        """
        if self._rknn_executor is not None:
            # RKNN Executor 的清理方法（如果有）
            if hasattr(self._rknn_executor, 'release'):
                self._rknn_executor.release()
            del self._rknn_executor
            self._rknn_executor = None
        
        print("[RKNNDetector] 模型已卸载")
    
    def get_info(self) -> Dict:
        """
        获取检测器信息
        
        Returns:
            检测器信息字典
        """
        info = {
            'backend': 'rknn',
            'model_path': self.config.model_path,
            'target_platform': self._rknn_target,
            'device_id': self._rknn_device_id,
            'core_mask': self._rknn_core_mask,
            'input_shape': self.input_shape,
            'class_names': self.config.class_names,
            'conf_threshold': self.config.conf_threshold,
            'iou_threshold': self.config.iou_threshold
        }
        
        # 添加推理统计信息
        stats = self.get_inference_stats()
        info.update(stats)
        
        return info


# ========================================
# 工具函数
# ========================================

def convert_onnx_to_rknn(
    onnx_path: str,
    rknn_path: str,
    target: str = 'rk3588',
    input_size: Tuple[int, int] = (640, 640),
    quantize: bool = True
):
    """
    将 ONNX 模型转换为 RKNN 模型
    
    此函数需要在 PC 上运行（需要 rknn-toolkit2-lite）。
    
    Args:
        onnx_path: ONNX 模型路径
        rknn_path: 输出的 RKNN 模型路径
        target: 目标平台（rk3588, rk3566, rk3568, rv1103, rv1106）
        input_size: 输入尺寸 (H, W)
        quantize: 是否量化为 INT8（推荐，性能更好）
    
    Returns:
        None
    
    Example:
        >>> convert_onnx_to_rknn(
        ...     onnx_path='models/yolov8n.onnx',
        ...     rknn_path='models/yolov8n.rknn',
        ...     target='rk3588',
        ...     quantize=True
        ... )
    """
    try:
        from rknn.api import RKNN
        
        print(f"[RKNN] 开始转换 ONNX 到 RKNN...")
        print(f"[RKNN]   输入：{onnx_path}")
        print(f"[RKNN]   输出：{rknn_path}")
        print(f"[RKNN]   目标：{target}")
        
        # 创建 RKNN 对象
        rknn = RKNN()
        
        # 配置模型
        rknn.config(
            target_platform=target,
            mean_values=[[0, 0, 0]],
            std_values=[[255, 255, 255]]
        )
        
        # 加载 ONNX 模型
        ret = rknn.load_onnx(model=onnx_path)
        if ret != 0:
            raise RuntimeError(f"加载 ONNX 模型失败：{ret}")
        
        # 构建 RKNN 模型
        ret = rknn.build(do_quantization=quantize)
        if ret != 0:
            raise RuntimeError(f"构建 RKNN 模型失败：{ret}")
        
        # 导出 RKNN 模型
        ret = rknn.export_rknn(rknn_path)
        if ret != 0:
            raise RuntimeError(f"导出 RKNN 模型失败：{ret}")
        
        # 清理
        rknn.release()
        
        print(f"[RKNN] 转换完成：{rknn_path}")
        
    except ImportError:
        print("[RKNN] rknn-toolkit2-lite 未安装，请先安装：")
        print("  pip install rknn-toolkit2-lite")
        raise