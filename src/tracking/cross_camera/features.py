"""
跨相机外观特征

为什么不用 ReID 模型：
    盒子算力要留给解码和检测。事件是"每次目标轨迹结束才产生一次"，
    用一次 OpenCV 颜色直方图（~1-3ms）就能拿到可用的外观描述，
    而且不引入新模型文件、新依赖。将来要升级成 osnet/RKNN，
    只需替换本模块的 extract_descriptor / compare 两个函数。

描述子布局（uint8，总量 148 字节，base64 后约 200 字符）：
    [0:48]    上半身 H-S-V 直方图（8 色相 × 3 饱和 × 2 明度）
    [48:96]   下半身 H-S-V 直方图
    [96:144]  整体   H-S-V 直方图
    [144]     宽高比（w/h，0.1~5 映射到 0~255）
    [145]     相对身高（bbox 高 / 画面高）
    [146]     上半身平均亮度
    [147]     下半身平均亮度

上半身/下半身分开统计的理由：上衣和裤子颜色通常不同，分区域能显著
降低"同款上衣不同裤子"被误关联的概率。
"""

import base64
import logging
from typing import Dict, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DESCRIPTOR_VERSION = 1

_HUE_BINS = 8          # OpenCV H ∈ [0,179]
_SAT_BINS = 3
_VAL_BINS = 2
_REGION_BINS = _HUE_BINS * _SAT_BINS * _VAL_BINS   # 48
_HIST_LEN = _REGION_BINS * 3                       # 144
_DESC_LEN = _HIST_LEN + 4                          # 148

# 裁剪尺寸（固定大小保证直方图可比，与相机分辨率无关）
_CROP_W = 64
_CROP_H = 128

# 各区域在外观相似度中的权重（上衣最能区分人）
_REGION_WEIGHTS = (0.45, 0.35, 0.20)


def _hist_of(crop_hsv: np.ndarray) -> np.ndarray:
    """计算单个区域的 H-S-V 3D 直方图，L1 归一化后映射到 0~255"""
    if crop_hsv.size == 0:
        return np.zeros(_REGION_BINS, dtype=np.uint8)
    hist = cv2.calcHist(
        [crop_hsv], [0, 1, 2], None,
        [_HUE_BINS, _SAT_BINS, _VAL_BINS],
        [0, 180, 0, 256, 0, 256],
    )
    hist = hist.reshape(-1).astype(np.float32)
    total = float(hist.sum())
    if total <= 0:
        return np.zeros(_REGION_BINS, dtype=np.uint8)
    hist /= total
    return np.clip(hist * 255.0, 0, 255).astype(np.uint8)


def extract_descriptor(image_rgb: np.ndarray, bbox,
                       margin: float = 0.0, inset: float = 0.05) -> Optional[str]:
    """从"全帧 + 检测框"提取外观描述子

    Args:
        image_rgb: 事件快照（RGB numpy 数组）
        bbox: [x1, y1, x2, y2]，原图坐标
        margin: 各方向外扩比例（默认不外扩，避免带进框外的背景）
        inset: 各方向内缩比例。事件图是**已经画了检测框/跟踪框/轨迹**的帧，
               内缩可以避开压在 bbox 边线上的 2~3px 彩色框线，避免污染直方图。

    Returns:
        base64 字符串；无法提取时返回 None（融合服务会退化到纯时空关联）
    """
    try:
        if image_rgb is None or bbox is None or len(bbox) < 4:
            return None
        if not isinstance(image_rgb, np.ndarray) or image_rgb.ndim < 2:
            return None

        frame_h, frame_w = image_rgb.shape[:2]
        if frame_h <= 0 or frame_w <= 0:
            return None

        x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
        bw, bh = x2 - x1, y2 - y1
        if bw <= 2 or bh <= 2:
            return None

        # 内缩：避开画在 bbox 边线上的框线
        inset_px = max(2.0, min(bw, bh) * min(max(inset, 0.0), 0.25))
        mx, my = bw * margin, bh * margin
        x1i = max(0, int(round(x1 + inset_px - mx)))
        y1i = max(0, int(round(y1 + inset_px - my)))
        x2i = min(frame_w, int(round(x2 - inset_px + mx)))
        y2i = min(frame_h, int(round(y2 - inset_px + my)))
        if x2i - x1i < 6 or y2i - y1i < 6:
            # 目标太小，退回不内缩
            x1i, y1i = max(0, int(round(x1))), max(0, int(round(y1)))
            x2i, y2i = min(frame_w, int(round(x2))), min(frame_h, int(round(y2)))
            if x2i - x1i < 4 or y2i - y1i < 4:
                return None

        crop = image_rgb[y1i:y2i, x1i:x2i]
        if crop.ndim == 2:
            crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2RGB)
        elif crop.shape[2] == 4:
            crop = cv2.cvtColor(crop, cv2.COLOR_RGBA2RGB)

        resized = cv2.resize(crop, (_CROP_W, _CROP_H), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(resized, cv2.COLOR_RGB2HSV)

        mid = _CROP_H // 2
        hist_upper = _hist_of(hsv[:mid, :, :])
        hist_lower = _hist_of(hsv[mid:, :, :])
        hist_full = _hist_of(hsv)

        # 形状/亮度标量
        aspect = bw / bh if bh > 0 else 1.0
        aspect_q = int(np.clip(aspect / 5.0, 0.0, 1.0) * 255)
        height_q = int(np.clip(bh / float(frame_h), 0.0, 1.0) * 255)
        v_upper = int(np.clip(float(hsv[:mid, :, 2].mean()), 0, 255))
        v_lower = int(np.clip(float(hsv[mid:, :, 2].mean()), 0, 255))

        desc = np.concatenate([
            hist_upper, hist_lower, hist_full,
            np.array([aspect_q, height_q, v_upper, v_lower], dtype=np.uint8),
        ]).astype(np.uint8)

        return base64.b64encode(desc.tobytes()).decode('ascii')
    except Exception as e:
        logger.debug(f"[CrossCamera] 外观特征提取失败: {e}")
        return None


def decode_descriptor(encoded: str) -> Optional[np.ndarray]:
    """解码描述子；格式不符返回 None"""
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded.encode('ascii'))
        if len(raw) != _DESC_LEN:
            return None
        return np.frombuffer(raw, dtype=np.uint8)
    except Exception:
        return None


def compare(desc_a, desc_b) -> Dict[str, float]:
    """比较两个描述子的外观/形状相似度

    Args:
        desc_a/desc_b: extract_descriptor 的返回值（base64）或已解码数组

    Returns:
        {'appearance': 0~1, 'shape': 0~1}；任一无法解码时两者都返回 0
    """
    arr_a = decode_descriptor(desc_a) if isinstance(desc_a, str) else desc_a
    arr_b = decode_descriptor(desc_b) if isinstance(desc_b, str) else desc_b
    if arr_a is None or arr_b is None:
        return {'appearance': 0.0, 'shape': 0.0}

    fa = arr_a.astype(np.float32)
    fb = arr_b.astype(np.float32)

    appearance = 0.0
    for region_idx, weight in enumerate(_REGION_WEIGHTS):
        start = region_idx * _REGION_BINS
        end = start + _REGION_BINS
        ha, hb = fa[start:end], fb[start:end]
        total_a, total_b = ha.sum(), hb.sum()
        if total_a <= 0 or total_b <= 0:
            continue
        # 直方图交集（等价 1 - 归一化 L1 距离的一半）
        inter = float(np.minimum(ha, hb).sum())
        appearance += weight * (inter / max(total_a, total_b))
    appearance = float(np.clip(appearance / sum(_REGION_WEIGHTS), 0.0, 1.0))

    # 形状先验：宽高比 + 相对身高 + 亮度接近度
    ar_a, ar_b = fa[144] / 255.0 * 5.0, fb[144] / 255.0 * 5.0
    ar_sim = 1.0 - min(1.0, abs(ar_a - ar_b) / max(ar_a, ar_b, 1e-3))
    h_a, h_b = fa[145] / 255.0, fb[145] / 255.0
    h_sim = 1.0 - min(1.0, abs(h_a - h_b) / max(h_a, h_b, 1e-3))
    v_sim = 1.0 - abs(float(fa[146:148].mean()) - float(fb[146:148].mean())) / 255.0
    shape = float(np.clip(0.45 * ar_sim + 0.35 * h_sim + 0.20 * v_sim, 0.0, 1.0))

    return {'appearance': appearance, 'shape': shape}
