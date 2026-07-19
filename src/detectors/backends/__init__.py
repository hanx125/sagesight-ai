"""
检测器后端模块

提供各种推理后端的实现：
- ONNX CPU
- ONNX CUDA
- ONNX TensorRT
- TensorRT
- RKNN
"""

from .onnx_cpu import ONNXCPUDetector
from .onnx_cuda import ONNXCUDADetector
from .onnx_tensorrt import ONNXTensorRTDetector
from .tensorrt import TensorRTDetector
from .rknn import RKNNDetector

__all__ = [
    'ONNXCPUDetector',
    'ONNXCUDADetector',
    'ONNXTensorRTDetector',
    'TensorRTDetector',
    'RKNNDetector'
]