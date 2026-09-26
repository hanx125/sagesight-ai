"""
模型输入尺寸探测

读取模型文件真实输入尺寸 (height, width)，供任务启动时校验并纠正配置值。

背景：预处理张量尺寸完全由配置（算法库 input_size → detector_config.input_width/height）
决定，而模型文件的输入形状在导出时即已固定。两者不一致时，推理会因输入形状不匹配
直接失败，任务表现为完全无检测结果。此处为所有后端提供统一的纠正依据。

返回值约定：
- (height, width)：模型固定输入尺寸
- None：无法离线读取（运行时库未安装）或为动态尺寸（符号维度）
"""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def resolve_input_hw(shape, layout: str = 'nchw') -> Optional[Tuple[int, int]]:
    """
    从模型输入形状解析固定 (height, width)

    Args:
        shape: 模型输入形状（含 batch 与通道维，共 4 维）
        layout: 'nchw'（ONNX / TensorRT）或 'nhwc'（RKNN）

    Returns:
        (height, width)；含符号维度（动态尺寸）或非 4 维时返回 None
    """
    if shape is None:
        return None

    dims = list(shape)
    if len(dims) != 4:
        return None

    if layout == 'nchw':
        h, w = dims[2], dims[3]
    else:
        h, w = dims[1], dims[2]

    if not isinstance(h, int) or not isinstance(w, int):
        return None
    if h <= 0 or w <= 0:
        return None
    return int(h), int(w)


def probe_input_shape(model_path: str) -> Optional[Tuple[int, int]]:
    """
    读取模型文件真实输入尺寸

    Args:
        model_path: 模型文件路径（.onnx / .engine / .rknn）

    Returns:
        (height, width)；无法读取时返回 None
    """
    if not model_path or not os.path.exists(model_path):
        return None

    suffix = os.path.splitext(model_path)[1].lower()
    try:
        if suffix == '.onnx':
            return _probe_onnx(model_path)
        if suffix in ('.engine', '.plan', '.trt'):
            return _probe_tensorrt_engine(model_path)
        if suffix == '.rknn':
            # .rknn 无公开的离线输入尺寸读取接口（rknn-toolkit-lite2 仅在推理时校验形状），
            # 无法探测，以算法库“输入尺寸”为唯一依据
            logger.debug(f"[模型输入尺寸探测] RKNN 模型不支持离线读取输入尺寸：{model_path}")
            return None
    except Exception as e:
        logger.debug(f"[模型输入尺寸探测] 读取失败 {model_path}：{e}")
    return None


def _probe_onnx(model_path: str) -> Optional[Tuple[int, int]]:
    """读取 ONNX 模型输入尺寸（仅建立会话读取形状，不做推理）"""
    import onnxruntime as ort

    sess_options = ort.SessionOptions()
    # 仅用于读取形状：关闭图优化、抑制日志，降低加载开销
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess_options.log_severity_level = 3

    session = ort.InferenceSession(
        model_path,
        sess_options=sess_options,
        providers=['CPUExecutionProvider'],
    )
    return resolve_input_hw(session.get_inputs()[0].shape, 'nchw')


def _probe_tensorrt_engine(model_path: str) -> Optional[Tuple[int, int]]:
    """读取 TensorRT Engine 输入尺寸"""
    import tensorrt as trt

    with open(model_path, 'rb') as f:
        engine_data = f.read()

    engine = trt.Runtime(trt.Logger(trt.Logger.ERROR)).deserialize_cuda_engine(engine_data)
    if engine is None:
        return None

    for i in range(engine.num_io_tensors):
        name = engine.get_tensor_name(i)
        if engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
            return resolve_input_hw(engine.get_tensor_shape(name), 'nchw')
    return None