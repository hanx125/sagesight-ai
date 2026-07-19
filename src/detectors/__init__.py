"""
检测器模块

提供目标检测功能，支持多种推理后端：

主要组件：
1. BaseDetector: 检测器基类（base.py）
2. DetectorConfig: 检测器配置（detector_config.py）
3. OptimizedPreprocessor: 预处理器（preprocessor.py）
4. 后端检测器：
   - ONNXCPUDetector: ONNX CPU检测器
   - ONNXCUDADetector: ONNX CUDA检测器
   - ONNXTensorRTDetector: ONNX TensorRT检测器
   - TensorRTDetector: TensorRT检测器
   - RKNNDetector: RKNN检测器
5. create_detector: 工厂函数（factory.py）

使用示例：
    from src.detectors import create_detector
    
    # 创建检测器
    detector = create_detector(
        model_path="models/yolov11n.onnx",
        backend="onnx_cuda"
    )
    
    # 初始化
    detector.initialize()
    
    # 执行检测
    results = detector.detect(input_tensor)
"""

# 基础类和数据结构
from .base import BaseDetector, Detection, DetectorConfig, load_detector_config
from .preprocessor import OptimizedPreprocessor

# 后端检测器（延迟导入，避免循环依赖）
__all__ = [
    # 基础类
    'BaseDetector',
    'Detection',
    'DetectorConfig',
    'OptimizedPreprocessor',
    
    # 工厂函数
    'create_detector',
    'create_detector_from_config',
    'get_available_backends',
    
    # 配置加载函数
    'load_detector_config',
    
    # 后端检测器（通过factory导入）
    'ONNXCPUDetector',
    'ONNXCUDADetector',
    'ONNXTensorRTDetector',
    'TensorRTDetector',
    'RKNNDetector',
]


def __getattr__(name):
    """
    延迟导入后端检测器，避免循环依赖
    
    Args:
        name: 属性名
    
    Returns:
        检测器类或工厂函数
    """
    # 工厂函数
    if name == 'create_detector':
        from .factory import create_detector
        return create_detector
    
    if name == 'create_detector_from_config':
        from .factory import create_detector_from_config
        return create_detector_from_config
    
    if name == 'get_available_backends':
        from .factory import get_available_backends
        return get_available_backends
    
    # 配置加载函数
    if name == 'load_detector_config':
        from .base import load_detector_config
        return load_detector_config
    
    # 后端检测器
    if name == 'ONNXCPUDetector':
        from .backends.onnx_cpu import ONNXCPUDetector
        return ONNXCPUDetector
    
    if name == 'ONNXCUDADetector':
        from .backends.onnx_cuda import ONNXCUDADetector
        return ONNXCUDADetector
    
    if name == 'ONNXTensorRTDetector':
        from .backends.onnx_tensorrt import ONNXTensorRTDetector
        return ONNXTensorRTDetector
    
    if name == 'TensorRTDetector':
        from .backends.tensorrt import TensorRTDetector
        return TensorRTDetector
    
    if name == 'RKNNDetector':
        from .backends.rknn import RKNNDetector
        return RKNNDetector
    
    raise AttributeError(f"module {__name__} has no attribute {name}")


# 兼容旧版本：保留 YOLODetector 作为别名
def YOLODetector(*args, **kwargs):
    """
    YOLODetector 别名（兼容旧版本）
    
    注意：推荐使用 create_detector() 或 create_detector_from_config()
    """
    from .factory import create_detector_from_config
    
    if len(args) > 0 and not isinstance(args[0], (dict, DetectorConfig)):
        # 旧版本调用方式：YOLODetector(config)
        config = args[0]
    elif 'config' in kwargs:
        config = kwargs['config']
    else:
        # 新版本调用方式：create_detector_from_config(config)
        config = DetectorConfig(**kwargs)
    
    return create_detector_from_config(config)