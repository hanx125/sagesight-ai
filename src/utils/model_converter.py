"""
模型转换服务

在任务启动时，将算法库上传的 PyTorch (.pt/.pth) 模型自动转换为目标推理后端所需格式。

目标格式按后端自动决定：
- onnx_cpu / onnx_cuda / onnx_tensorrt / tensorrt：PT → ONNX（单张量解码输出）
- rknn：PT → ONNX → RKNN（RK3588 等瑞芯微 NPU）

转换结果按「源 PT 文件 + 目标格式 + 后端参数」缓存，避免任务反复启动时重复转换。

说明（后端输出格式差异）：
- ONNX 后端：期望单张量输出 shape (1, C+4, N)，坐标为解码后的 xywh（输入尺度、未归一化）。
  ultralytics 标准 `YOLO(pt).export(format='onnx')` 即满足此格式，可直接解析（见 base._postprocess_yolo）。
- RKNN 后端：本项目的 RKNNDetector._postprocess_yolo11_rknn 期望 6 张量
  （3 尺度 × {box_dfl, class_conf}），对应 rknn_model_zoo 的 YOLO 导出格式。
  因此 RKNN 转换需在导出 ONNX 时保留 Detect 头的中间输出，再由 rknn-toolkit2 量化烘焙。
"""

import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ========================================
# 常量
# ========================================

# RKNN 目标平台（默认 rk3588）
DEFAULT_RKNN_TARGET = "rk3588"
# 默认输入尺寸（未指定时）
DEFAULT_INPUT_SIZE = 640

# 转换输出目录
CONVERT_DIR_NAME = "converted"


# ========================================
# 工具函数
# ========================================

def is_pt_model(model_path: str) -> bool:
    """判断模型文件是否为 PyTorch (.pt/.pth) 格式"""
    return bool(model_path) and model_path.lower().endswith(('.pt', '.pth'))


def _target_format_for_backend(backend: str) -> str:
    """根据推理后端返回目标模型格式（'onnx' 或 'rknn'）"""
    if backend == 'rknn':
        return 'rknn'
    # onnx_cpu/onnx_cuda/onnx_tensorrt/tensorrt 均基于 onnx 推理
    return 'onnx'


def _parse_input_size(input_size: str, default: int = DEFAULT_INPUT_SIZE) -> tuple:
    """解析 '640x640' / '640*640' 为 (width, height)，失败返回默认值"""
    try:
        parts = str(input_size).lower().replace('*', 'x').split('x')
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return default, default


def _converted_dir() -> Path:
    """转换输出目录：models/converted"""
    d = Path('models') / CONVERT_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _conversion_cache_key(pt_path: str, target_format: str, input_size: str,
                          rknn_target: Optional[str] = None) -> str:
    """生成转换缓存的唯一 key（基于源文件内容哈希 + 目标格式 + 关键参数）"""
    src = Path(pt_path)
    # 计算源 PT 文件内容哈希，检测到同路径文件被重新上传后自动重新转换
    try:
        hasher = hashlib.md5()
        hasher.update(src.read_bytes())
        content_hash = hasher.hexdigest()[:12]
    except Exception:
        content_hash = "unknown"
    parts = [src.stem, target_format, content_hash, str(input_size)]
    if target_format in ('rknn', 'onnx_6t'):
        parts.append(rknn_target or DEFAULT_RKNN_TARGET)
    return "_".join(parts)


def _converted_path(pt_path: str, target_format: str, input_size: str,
                    rknn_target: Optional[str] = None) -> Path:
    """返回转换目标文件的完整路径"""
    key = _conversion_cache_key(pt_path, target_format, input_size, rknn_target)
    ext = 'rknn' if target_format == 'rknn' else 'onnx'
    return _converted_dir() / f"{key}.{ext}"


# ========================================
# PT → ONNX
# ========================================

def convert_pt_to_onnx(pt_path: str, output_path: str, input_size: str,
                       model_type: str = "yolo11") -> str:
    """将 PyTorch 模型转换为 ONNX（ultralytics 标准导出）

    Args:
        pt_path: 源 PT 模型路径
        output_path: 输出 ONNX 路径
        input_size: 输入尺寸 "640x640"
        model_type: 模型类型（yolo11/yolov8/yolov5 等，用于加载网络结构）

    Returns:
        输出 ONNX 路径

    Raises:
        FileNotFoundError: PT 文件不存在
        RuntimeError: 转换失败（如 ultralytics 未安装、模型不支持导出）
    """
    if not os.path.exists(pt_path):
        raise FileNotFoundError(f"PT 模型不存在: {pt_path}")

    width, height = _parse_input_size(input_size)

    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError(
            "未安装 ultralytics，无法完成 PT→ONNX 转换。"
            "请执行: pip install ultralytics"
        ) from e

    logger.info(
        f"[ModelConverter] PT→ONNX 开始转换: {pt_path} "
        f"(imgsz={width}x{height}, model_type={model_type})"
    )

    try:
        model = YOLO(pt_path)
        # 使用单张量解码输出的标准导出（不附加 NMS，与项目 ONNX 后处理对齐）
        result = model.export(
            format="onnx",
            imgsz=(height, width),
            opset=12,
            simplify=True,
            dynamic=False,
        )
    except Exception as e:
        raise RuntimeError(f"PT→ONNX 转换失败: {e}") from e

    if isinstance(result, (list, tuple)):
        result = result[0] if result else output_path

    onnx_file = Path(str(result))
    # 若导出路径与预期不一致，复制到统一缓存目录
    if str(onnx_file.resolve()) != str(Path(output_path).resolve()):
        shutil.move(str(onnx_file), output_path)

    logger.info(f"[ModelConverter] PT→ONNX 完成: {output_path}")
    return output_path


# ========================================
# PT → ONNX（rknn_model_zoo 6 张量格式）
# ========================================

def convert_pt_to_onnx_6t(pt_path: str, output_path: str, input_size: str,
                          model_type: str = "yolo11") -> str:
    """将 PyTorch 模型导出为 rknn_model_zoo 兼容的 6 张量 ONNX

    项目 RKNNDetector（rknn.py）与 rknn_model_zoo 的 yolo11 后处理期望的输入格式为：
    3 个尺度 × { box_dfl: [1, 4*reg_max, H, W], class_conf: [1, nc, H, W] }。

    ultralytics 的标准导出是「解码后合并单张量」，不满足该格式；本函数直接抓取
    Detect 头中 cv2（box 分支）与 cv3（class 分支）的**中间卷积输出**，得到
    rknn_model_zoo 要求的 6 张量（DFL 解码在 RKNN 侧/项目后处理中完成）。

    Args:
        pt_path: 源 PT 模型路径
        output_path: 输出 ONNX 路径
        input_size: 输入尺寸 "640x640"
        model_type: 模型类型（yolo11 等，用于加载网络结构）

    Returns:
        输出 ONNX 路径

    Raises:
        RuntimeError: 转换失败（ultralytics 未安装 / 模型结构不支持）
    """
    if not os.path.exists(pt_path):
        raise FileNotFoundError(f"PT 模型不存在: {pt_path}")

    width, height = _parse_input_size(input_size)

    try:
        import torch
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError(
            "未安装 torch 或 ultralytics，无法完成 6 张量 ONNX 导出。"
            "请执行: pip install torch ultralytics"
        ) from e

    import torch.nn as nn

    logger.info(
        f"[ModelConverter] PT→ONNX(6张量) 开始导出: {pt_path} "
        f"(imgsz={width}x{height}, model_type={model_type})"
    )

    try:
        model = YOLO(pt_path)
        # model.model 是 ultralytics DetectionModel，.model 为其层序列（nn.Sequential）
        layer_seq = model.model.model
        detect_head = layer_seq[-1]

        if not hasattr(detect_head, 'cv2') or not hasattr(detect_head, 'cv3'):
            raise RuntimeError(
                f"模型结构不支持 6 张量导出（未找到 Detect 头 cv2/cv3），model_type={model_type}"
            )

        nl = len(detect_head.cv2)

        class _ExportWrapper(nn.Module):
            """复现 ultralytics _predict_once 前向，取 Detect 头 cv2/cv3 中间输出

            ultralytics 各层通过 m.f 从历史输出 y 取特征（feature 记账机制），
            不能直接切片 backbone 运行。此处完整遍历所有层，仅对最后 Detect 头
            改为输出 cv2(DFL box) 与 cv3(class) 的中间卷积结果，共 6 张量。
            """

            def __init__(self, layer_seq, detect_head):
                super().__init__()
                self.layers = layer_seq  # 以子模块形式挂载，便于 onnx 追踪参数
                self.detect = detect_head

            def forward(self, x):
                y = []  # 各层输出缓存（未被保存的层为 None）
                for m in self.layers:
                    # 按 m.f 从历史输出取输入（与 ultralytics _predict_once 一致）
                    if m.f != -1:
                        if isinstance(m.f, int):
                            x = y[m.f]
                        else:
                            x = [x if j == -1 else y[j] for j in m.f]
                    if m is self.detect:
                        # Detect 头：x 为 [P3, P4, P5] 多尺度特征列表。
                        # cv2(DFL box) 保持原始卷积输出，DFL softmax 由后处理完成；
                        # cv3(class) 必须带 sigmoid：后处理直接拿它与阈值比较，
                        # 不带 sigmoid 得到的是 raw logits（会 >1），阈值语义与生产模型不一致。
                        feats = x
                        outputs = []
                        for i in range(self.detect.nl):
                            outputs.append(self.detect.cv2[i](feats[i]))
                            outputs.append(torch.sigmoid(self.detect.cv3[i](feats[i])))
                        return tuple(outputs)
                    x = m(x)
                    y.append(x if m.i in model.model.save else None)
                return x

        wrapper = _ExportWrapper(layer_seq, detect_head)
        wrapper.eval()

        dtype = next(wrapper.parameters()).dtype
        device = next(wrapper.parameters()).device
        dummy = torch.zeros(1, 3, height, width, dtype=dtype).to(device)

        torch.onnx.export(
            wrapper,
            dummy,
            output_path,
            input_names=["images"],
            output_names=[f"output{i}" for i in range(2 * nl)],
            opset_version=12,
            dynamic_axes=None,
        )

        # 校验输出数量与形状
        import onnx as _onnx
        _m = _onnx.load(output_path)
        n_out = len(_m.graph.output)
        if n_out != 6:
            raise RuntimeError(
                f"6 张量导出失败：期望 6 个输出，实际得到 {n_out} 个。"
                f"请改用 rknn_model_zoo 提供的优化导出方案。"
            )
        logger.info(f"[ModelConverter] 6 张量 ONNX 导出校验通过（{n_out} 个输出）")
    except Exception as e:
        raise RuntimeError(f"PT→ONNX(6张量) 导出失败: {e}") from e

    logger.info(f"[ModelConverter] PT→ONNX(6张量) 完成: {output_path}")
    return output_path


# ========================================
# ONNX → RKNN
# ========================================

def convert_onnx_to_rknn(onnx_path: str, output_path: str, target: str,
                         input_size: str, quantize: bool = True,
                         dataset_path: Optional[str] = None) -> str:
    """将 ONNX 模型转换为 RKNN

    Args:
        onnx_path: 源 ONNX 路径
        output_path: 输出 RKNN 路径
        target: 目标平台（rk3588 等）
        input_size: 输入尺寸（用于配置预处理）
        quantize: 是否 INT8 量化（默认 True）
        dataset_path: 量化数据集图片列表文件（量化推荐）

    Returns:
        输出 RKNN 路径
    """
    try:
        from rknn.api import RKNN
    except ImportError as e:
        raise RuntimeError(
            "未安装 rknn-toolkit2，无法完成 ONNX→RKNN 转换。"
            "请执行: pip install rknn-toolkit2，或在 PC 端转换后再上传 .rknn 文件"
        ) from e

    width, height = _parse_input_size(input_size)

    logger.info(
        f"[ModelConverter] ONNX→RKNN 开始转换: {onnx_path} "
        f"(target={target}, imgsz={width}x{height}, int8={quantize})"
    )

    rknn = RKNN()
    try:
        # 配置：与项目 rknn 后端预处理一致（RGB，0-255 归一化烘焙进模型）
        ret = rknn.config(
            target_platform=target,
            mean_values=[[0, 0, 0]],
            std_values=[[255, 255, 255]],
        )
        if ret != 0:
            raise RuntimeError(f"RKNN config 失败: {ret}")

        ret = rknn.load_onnx(model=onnx_path)
        if ret != 0:
            raise RuntimeError(f"RKNN load_onnx 失败: {ret}")

        if quantize:
            if dataset_path and os.path.exists(dataset_path):
                ret = rknn.build(do_quantization=True, dataset=dataset_path)
            else:
                logger.warning("[ModelConverter] 未提供量化数据集，使用随机数据量化")
                ret = rknn.build(do_quantization=True)
        else:
            ret = rknn.build(do_quantization=False)
        if ret != 0:
            raise RuntimeError(f"RKNN build 失败: {ret}")

        ret = rknn.export_rknn(output_path)
        if ret != 0:
            raise RuntimeError(f"RKNN export 失败: {ret}")
    finally:
        rknn.release()

    logger.info(f"[ModelConverter] ONNX→RKNN 完成: {output_path}")
    return output_path


# ========================================
# 统一入口
# ========================================

def ensure_converted_model(model_path: str, backend: str, input_size: str = "640x640",
                           rknn_target: Optional[str] = None,
                           model_type: str = "yolo11",
                           rknn_dataset: Optional[str] = None) -> str:
    """确保获得目标后端可用的模型路径

    若 model_path 为 .pt/.pth，则按 backend 自动转换为 onnx/rknn 并缓存；
    否则原样返回（已有可用模型）。

    Args:
        model_path: 算法模型路径（可能为 .pt/.onnx/.rknn）
        backend: 推理后端（onnx_cuda / rknn 等）
        input_size: 模型输入尺寸
        rknn_target: RKNN 目标平台（仅 backend=rknn 时使用）
        model_type: PT 模型类型（加载网络结构用）
        rknn_dataset: RKNN 量化数据集路径（可选）

    Returns:
        转换后可用模型路径

    Raises:
        RuntimeError: 转换失败
    """
    if not is_pt_model(model_path):
        return model_path

    target_format = _target_format_for_backend(backend)
    out_path = _converted_path(model_path, target_format, input_size,
                                rknn_target if target_format == 'rknn' else None)

    # 命中缓存直接返回
    if out_path.exists():
        logger.info(f"[ModelConverter] 命中缓存: {out_path}")
        return str(out_path)

    logger.info(
        f"[ModelConverter] 检测到 PT 模型，开始自动转换: {model_path} "
        f"(backend={backend}, target={target_format})"
    )

    if target_format == 'rknn':
        # RKNN 后端需要 6 张量（DFL box + class）输出，使用 rknn_model_zoo 兼容的导出方案
        tmp_onnx_6t = _converted_dir() / f"{_conversion_cache_key(model_path, 'onnx_6t', input_size, rknn_target)}.onnx"
        if not tmp_onnx_6t.exists():
            convert_pt_to_onnx_6t(model_path, str(tmp_onnx_6t), input_size, model_type)
        convert_onnx_to_rknn(
            str(tmp_onnx_6t), str(out_path),
            target=rknn_target or DEFAULT_RKNN_TARGET,
            input_size=input_size,
            dataset_path=rknn_dataset,
        )
        return str(out_path)

    # ONNX 后端：PT → 单张量 ONNX（原子性：成功后移动到最终缓存路径）
    tmp_onnx = _converted_dir() / f"{_conversion_cache_key(model_path, 'onnx', input_size)}.onnx"
    convert_pt_to_onnx(model_path, str(tmp_onnx), input_size, model_type)
    if str(tmp_onnx) != str(out_path):
        shutil.move(str(tmp_onnx), str(out_path))
    return str(out_path)