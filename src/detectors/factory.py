"""
检测器工厂模块

提供统一的检测器创建接口，根据配置自动选择合适的后端。
"""

import os
from typing import Union, Dict
from .base import BaseDetector, Detection, DetectorConfig


def create_detector(
    model_path: str,
    model_type: str = "yolov8n",
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    backend: str = "onnx_cuda",
    tensorrt_engine_path: str = "",
    tensorrt_engine_cache_path: str = "./trt_cache",
    use_fp16: bool = True,
    **kwargs
) -> BaseDetector:
    """
    创建检测器（工厂函数）
    
    Args:
        model_path: ONNX 模型路径
        model_type: 模型类型（yolov8n 或 yolo11n）
        conf_threshold: 置信度阈值
        iou_threshold: NMS IoU 阈值
        backend: 推理后端
            - "onnx_cpu": ONNX Runtime CPU（纯CPU推理）
            - "onnx_cuda": ONNX Runtime + CUDA（GPU加速）
            - "onnx_tensorrt": ONNX Runtime + TensorRT（自动转换+FP16）
            - "tensorrt": 直接加载 TensorRT Engine
            - "rknn": RKNN模型（RK3588等平台）
        tensorrt_engine_path: TensorRT Engine 路径（backend="tensorrt" 时使用）
        tensorrt_engine_cache_path: TensorRT Engine 缓存路径
        use_fp16: 使用 FP16 精度（仅 TensorRT）
        **kwargs: 其他配置参数
    
    Returns:
        检测器实例
    
    Example:
        >>> detector = create_detector(
        ...     model_path="models/yolov11n.onnx",
        ...     backend="onnx_cuda",
        ...     conf_threshold=0.25
        ... )
        >>> detector.initialize()
        >>> results = detector.detect(input_tensor)
    """
    # 创建配置对象
    config = DetectorConfig(
        model_path=model_path,
        model_type=model_type,
        conf_threshold=conf_threshold,
        iou_threshold=iou_threshold,
        backend=backend,
        tensorrt_engine_path=tensorrt_engine_path,
        tensorrt_engine_cache_path=tensorrt_engine_cache_path,
        use_fp16=use_fp16,
        **kwargs
    )
    
    # 根据后端选择检测器类
    detector_class = _get_detector_class(backend)
    
    # 创建检测器实例
    detector = detector_class(config)
    
    # 加载模型
    detector.load_model()
    
    return detector


def create_detector_from_config(config: Union[DetectorConfig, Dict]) -> BaseDetector:
    """
    从配置对象创建检测器
    
    Args:
        config: DetectorConfig 对象或字典
    
    Returns:
        检测器实例
    
    Example:
        >>> config = DetectorConfig(
        ...     model_path="models/yolov11n.onnx",
        ...     backend="onnx_cuda"
        ... )
        >>> detector = create_detector_from_config(config)
        >>> detector.initialize()
    """
    # 字典转 DetectorConfig
    if isinstance(config, dict):
        # 处理 class_names 类型转换（List → Tuple）
        if 'class_names' in config and isinstance(config['class_names'], list):
            config['class_names'] = tuple(config['class_names'])
        
        config = DetectorConfig(**config)
    
    # 根据后端选择检测器类
    detector_class = _get_detector_class(config.backend)
    
    # 创建检测器实例
    detector = detector_class(config)
    
    return detector


def _get_detector_class(backend: str):
    """
    根据后端类型获取检测器类
    
    Args:
        backend: 后端类型
    
    Returns:
        检测器类
    
    Raises:
        ValueError: 不支持的后端类型
    """
    from .backends.onnx_cpu import ONNXCPUDetector
    from .backends.onnx_cuda import ONNXCUDADetector
    from .backends.onnx_tensorrt import ONNXTensorRTDetector
    from .backends.tensorrt import TensorRTDetector
    from .backends.rknn import RKNNDetector
    
    backend_map = {
        'onnx_cpu': ONNXCPUDetector,
        'onnx_cuda': ONNXCUDADetector,
        'onnx_tensorrt': ONNXTensorRTDetector,
        'tensorrt': TensorRTDetector,
        'rknn': RKNNDetector,
    }
    
    if backend not in backend_map:
        raise ValueError(
            f"不支持的后端类型: {backend}\n"
            f"支持的后端: {list(backend_map.keys())}"
        )
    
    return backend_map[backend]


def get_available_backends() -> list:
    """
    获取当前系统可用的检测器后端
    
    Returns:
        可用的后端列表
    
    Example:
        >>> backends = get_available_backends()
        >>> print(backends)
        ['onnx_cpu', 'onnx_cuda', 'onnx_tensorrt']
    """
    available = ['onnx_cpu']  # CPU始终可用
    
    # 检查 CUDA
    try:
        import torch
        if torch.cuda.is_available():
            available.append('onnx_cuda')
    except ImportError:
        pass
    
    # 检查 TensorRT
    try:
        import tensorrt
        available.append('onnx_tensorrt')
        available.append('tensorrt')
    except ImportError:
        pass
    
    # 检查 RKNN
    try:
        from src.utils.rknn_executor import is_rknn_available
        if is_rknn_available():
            available.append('rknn')
    except ImportError:
        pass
    
    return available


def benchmark_backends(model_path: str, input_shape: tuple = (1, 3, 640, 640)):
    """
    对比所有可用后端的性能
    
    Args:
        model_path: 模型路径
        input_shape: 输入形状
    
    Returns:
        性能对比结果字典
    """
    import numpy as np
    import time
    
    results = {}
    available_backends = get_available_backends()
    
    print(f"[Benchmark] 可用后端: {available_backends}")
    
    # 创建测试输入
    input_tensor = np.random.rand(*input_shape).astype(np.float32)
    
    for backend in available_backends:
        try:
            print(f"\n[Benchmark] 测试后端: {backend}")
            
            # 创建检测器
            detector = create_detector(
                model_path=model_path,
                backend=backend
            )
            
            # 预热（10帧）
            for _ in range(10):
                detector.infer(input_tensor)
            
            # 性能测试（100帧）
            start_time = time.time()
            for _ in range(100):
                detector.infer(input_tensor)
            elapsed = time.time() - start_time
            
            # 统计
            stats = detector.get_inference_stats()
            
            results[backend] = {
                'avg_inference_time_ms': stats['avg_inference_time_ms'],
                'fps': stats['fps'],
                'total_objects': 0
            }
            
            print(f"  平均推理时间: {stats['avg_inference_time_ms']:.2f}ms")
            print(f"  FPS: {stats['fps']:.1f}")
            
            # 卸载模型
            detector.unload_model()
            
        except Exception as e:
            print(f"  [错误] {backend} 测试失败: {e}")
            results[backend] = {'error': str(e)}
    
    return results