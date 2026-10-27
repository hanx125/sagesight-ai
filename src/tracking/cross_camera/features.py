"""跨相机外观特征

两种后端，按配置切换，对外接口不变：

    reid       —— ONNX ReID 模型（默认）。判别力强，能区分"同色不同人"，
                  单次推理 10~80ms，只在轨迹结束时跑一次。
    histogram  —— OpenCV HSV 三区域直方图（兜底）。1~3ms，零依赖，
                  模型缺失 / onnxruntime 未装 / 推理异常时自动退回。

描述子格式自描述，读的时候不用问数据库就能判断版本：

    v1（148 字节，无 header，历史数据）：
        [0:48]    上半身 H-S-V 直方图（8 色相 × 3 饱和 × 2 明度）
        [48:96]   下半身直方图
        [96:144]  整体直方图
        [144]     宽高比（w/h，0.1~5 映射到 0~255）
        [145]     相对身高（bbox 高 / 画面高）
        [146]     上半身平均亮度
        [147]     下半身平均亮度

    v2（14 + 2×dim 字节，带 magic）：
        [0:2]     b'CR'            magic
        [2]       0x02             version
        [3]       flags            bit0 = 已 L2 归一化
        [4:6]     dim              uint16 小端，特征维度
        [6:10]    形状四元组        同 v1 的 144~147
        [10:14]   mu_hash          uint32，特征中心化均值的指纹（0 = 未中心化）
        [14:]     float16 × dim    特征向量

    用 float16 而不是 float32：余弦相似度的量化误差约 1e-3，
    对 0.58 这种阈值完全够用，存储省一半。

    为什么带 mu_hash：
        中心化均值变了，特征空间就变了（哪怕模型没变）。把均值指纹写进
        描述子，比较时指纹不同直接判不可比，避免"旧均值特征 + 新均值特征"
        混算出一个看起来正常、其实毫无意义的相似度。

跨版本比较：
    同一目标的两段轨迹若一段是 v1、一段是 v2，特征空间不同不能直接比。
    compare() 会返回 compatible=0，匹配器据此退化为纯时空关联
    （宁可不比，也不能拿不可比的东西硬算相似度）。
    切换后端后走一次"重建"，把 events.cross_feature 清空重算即可全部升到 v2。

上半身/下半身分开统计（仅 histogram 后端）的理由：上衣和裤子颜色通常不同，
分区域能显著降低"同款上衣不同裤子"被误关联的概率。
"""

import base64
import logging
import struct
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DESCRIPTOR_VERSION = 1          # 直方图描述子版本
REID_VERSION = 2                # ReID 描述子版本

_MAGIC = b'CR'
_V2_HEADER = 14
_FLAG_NORMALIZED = 1

# ---- v1 直方图参数 ----
_HUE_BINS = 8          # OpenCV H ∈ [0,179]
_SAT_BINS = 3
_VAL_BINS = 2
_REGION_BINS = _HUE_BINS * _SAT_BINS * _VAL_BINS   # 48
_HIST_LEN = _REGION_BINS * 3                       # 144
_DESC_LEN = _HIST_LEN + 4                          # 148
_CROP_W = 64
_CROP_H = 128
_REGION_WEIGHTS = (0.45, 0.35, 0.20)   # 上衣最能区分人

# ---- 默认配置（配置中心读不到时的兜底）----
_DEFAULT_FEATURE_CFG = {
    'backend': 'reid',
    'model_path': 'models/reid/reid_resnet18.onnx',
    'input_width': 128,
    'input_height': 256,
    'num_threads': 2,
    'min_crop_px': 24,
    'min_sharpness': 0.0,
}


def get_feature_config() -> Dict:
    """读 feature 段配置；导入失败时用默认值，保证模块可独立测试"""
    try:
        from src.tracking.cross_camera.config import get_cross_camera_config
        cfg = get_cross_camera_config() or {}
        feat = cfg.get('feature') or {}
        merged = dict(_DEFAULT_FEATURE_CFG)
        merged.update({k: v for k, v in feat.items() if v is not None})
        return merged
    except Exception:
        return dict(_DEFAULT_FEATURE_CFG)


def backend_name() -> str:
    name = str(get_feature_config().get('backend') or 'reid').strip().lower()
    return name if name in ('reid', 'histogram') else 'reid'


# ========================================
# 裁剪
# ========================================

def crop_target(image_rgb: np.ndarray, bbox,
                margin: float = 0.0, inset: float = 0.05) -> Optional[np.ndarray]:
    """按 bbox 裁剪目标区域

    inset 默认 0.05：事件图是**已经画了检测框/跟踪框/轨迹**的全帧，
    内缩可以避开压在 bbox 边线上的 2~3px 彩色框线，否则框线像素会污染特征。
    """
    if image_rgb is None or bbox is None or len(bbox) < 4:
        return None
    if not isinstance(image_rgb, np.ndarray) or image_rgb.ndim < 2:
        return None

    frame_h, frame_w = image_rgb.shape[:2]
    if frame_h <= 0 or frame_w <= 0:
        return None

    try:
        x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    except (TypeError, ValueError):
        return None
    bw, bh = x2 - x1, y2 - y1
    if bw <= 2 or bh <= 2:
        return None

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
    if crop.size == 0:
        return None
    if crop.ndim == 2:
        crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2RGB)
    elif crop.ndim == 3 and crop.shape[2] == 4:
        crop = cv2.cvtColor(crop, cv2.COLOR_RGBA2RGB)
    return crop


def _shape_meta(bbox, frame_h: int) -> Tuple[int, int, int, int]:
    """形状四元组：宽高比 / 相对身高 / 上亮度 / 下亮度（后两项由调用方填）"""
    x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    bw, bh = max(x2 - x1, 1e-3), max(y2 - y1, 1e-3)
    aspect_q = int(np.clip((bw / bh) / 5.0, 0.0, 1.0) * 255)
    height_q = int(np.clip(bh / float(max(frame_h, 1)), 0.0, 1.0) * 255)
    return aspect_q, height_q, 0, 0


def fit_bbox_to_image(bbox: Sequence[float], image_shape, orig_width: int,
                      orig_height: int) -> Tuple[Optional[List[float]], float]:
    """把 bbox 换算到"成品图"坐标系

    事件图落盘时会等比缩放到 640x480 以内，但 bbox 记的是原图坐标。
    不换算直接裁，位置会整体偏移甚至裁到画面外（实测 1440 的原图缩到 480 后，
    y=569 的框直接落在 480 高的画面之外，裁剪必定失败）。

    Args:
        bbox: 原图坐标系的 [x1, y1, x2, y2]
        image_shape: 成品图 shape（读盘得到，两个元素即可）
        orig_width/orig_height: 原图尺寸（事件的 image_width/image_height）

    Returns:
        (bbox, inset) —— bbox 为 None 表示不裁；inset 是建议的内缩比例
    """
    img_h, img_w = int(image_shape[0]), int(image_shape[1])
    try:
        w0, h0 = int(orig_width or 0), int(orig_height or 0)
    except (TypeError, ValueError):
        w0 = h0 = 0

    if w0 > 0 and h0 > 0 and (abs(img_w - w0) > 2 or abs(img_h - h0) > 2):
        sx, sy = img_w / float(w0), img_h / float(h0)
        return [bbox[0] * sx, bbox[1] * sy, bbox[2] * sx, bbox[3] * sy], 0.05

    # 没有原图尺寸（旧事件）。bbox 明显超出画面 → 这张图本身就是目标裁剪图
    # （人脸/车牌库比对走的是裁剪路径），整张图就是目标。
    if bbox[2] > img_w + 2 or bbox[3] > img_h + 2:
        return [0.0, 0.0, float(img_w), float(img_h)], 0.02

    return [float(v) for v in bbox[:4]], 0.05


# ========================================
# v1 直方图
# ========================================

def _hist_of(crop_hsv: np.ndarray) -> np.ndarray:
    """单个区域的 H-S-V 3D 直方图，L1 归一化后映射到 0~255"""
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


def _histogram_descriptor(crop: np.ndarray, bbox, frame_h: int) -> bytes:
    resized = cv2.resize(crop, (_CROP_W, _CROP_H), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(resized, cv2.COLOR_RGB2HSV)
    mid = _CROP_H // 2
    hist_upper = _hist_of(hsv[:mid, :, :])
    hist_lower = _hist_of(hsv[mid:, :, :])
    hist_full = _hist_of(hsv)

    aspect_q, height_q, _, _ = _shape_meta(bbox, frame_h)
    v_upper = int(np.clip(float(hsv[:mid, :, 2].mean()), 0, 255))
    v_lower = int(np.clip(float(hsv[mid:, :, 2].mean()), 0, 255))

    desc = np.concatenate([
        hist_upper, hist_lower, hist_full,
        np.array([aspect_q, height_q, v_upper, v_lower], dtype=np.uint8),
    ]).astype(np.uint8)
    return desc.tobytes()


# ========================================
# v2 ReID
# ========================================

def _encode_v2(vector: np.ndarray, meta: Sequence[int], mu_hash: int = 0) -> bytes:
    vec = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    flags = 0
    if norm > 1e-6:
        vec = vec / norm
        flags |= _FLAG_NORMALIZED
    dim = int(vec.shape[0])
    aspect_q, height_q, v_up, v_lo = [int(np.clip(v, 0, 255)) for v in meta[:4]]
    header = (_MAGIC + bytes([REID_VERSION, flags]) + struct.pack('<H', dim) +
              bytes([aspect_q, height_q, v_up, v_lo]) +
              struct.pack('<I', int(mu_hash) & 0xFFFFFFFF))
    return header + vec.astype(np.float16).tobytes()


def _reid_descriptor(crops: List[np.ndarray], bbox, frame_h: int,
                     cfg: Dict) -> Optional[bytes]:
    """用 ReID 模型提取特征；不可用时返回 None"""
    try:
        from src.tracking.cross_camera.reid import get_backend
        backend = get_backend()
        backend.configure(
            str(cfg.get('model_path') or _DEFAULT_FEATURE_CFG['model_path']),
            int(cfg.get('input_width') or 128),
            int(cfg.get('input_height') or 256),
            int(cfg.get('num_threads') or 2),
        )
        if not backend.ready:
            return None
        feats = backend.embed(crops)
        if feats is None or feats.shape[0] == 0:
            return None
        # 多帧：先平均再归一化，比单帧稳定（抖动/遮挡会被摊平）
        vec = feats.mean(axis=0) if feats.shape[0] > 1 else feats[0]
        aspect_q, height_q, _, _ = _shape_meta(bbox, frame_h)
        # 亮度信息对 ReID 无意义，但对"形状先验"里的亮度相似度有用，留着
        v_up, v_lo = _brightness_pair(crops[0])
        return _encode_v2(vec, (aspect_q, height_q, v_up, v_lo), backend.mean_hash)
    except Exception as e:
        logger.debug(f"[CrossCamera] ReID 特征提取失败: {e}")
        return None


def _brightness_pair(crop: np.ndarray) -> Tuple[int, int]:
    try:
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        mid = gray.shape[0] // 2
        up = int(np.clip(float(gray[:mid].mean()), 0, 255)) if mid > 0 else 0
        lo = int(np.clip(float(gray[mid:].mean()), 0, 255)) if mid > 0 else 0
        return up, lo
    except Exception:
        return 0, 0


# ========================================
# 对外接口
# ========================================

def extract_descriptor(image_rgb: np.ndarray, bbox,
                       margin: float = 0.0, inset: float = 0.05) -> Optional[str]:
    """从"全帧 + 检测框"提取外观描述子

    Args:
        image_rgb: 事件快照（RGB numpy 数组）
        bbox: [x1, y1, x2, y2]，原图坐标
        margin: 各方向外扩比例（默认不外扩，避免带进框外背景）
        inset: 各方向内缩比例，默认 0.05 用于避开已绘制的框线

    Returns:
        base64 字符串；无法提取时返回 None（融合服务会退化到纯时空关联）
    """
    frames = [(image_rgb, bbox)]
    return extract_descriptor_multi(frames, margin=margin, inset=inset)


def extract_descriptor_multi(frames: Sequence[Tuple[np.ndarray, Sequence]],
                             margin: float = 0.0,
                             inset: float = 0.05) -> Optional[str]:
    """多帧提取（同一轨迹的多张高质量快照）

    多帧只对 ReID 有意义：特征取均值后重新归一化，能摊平单帧的姿态/
    遮挡/运动模糊。直方图后端只用第一帧（平均直方图收益很小）。

    Args:
        frames: [(image, bbox), ...]，按质量从高到低排列
    """
    if not frames:
        return None
    cfg = get_feature_config()
    use_reid = backend_name() == 'reid'
    min_crop = int(cfg.get('min_crop_px') or 0)
    min_sharp = float(cfg.get('min_sharpness') or 0.0)

    crops: List[np.ndarray] = []
    first_bbox = None
    first_frame_h = 0
    for image, bbox in frames:
        crop = crop_target(image, bbox, margin=margin, inset=inset)
        if crop is None:
            continue
        h, w = crop.shape[:2]
        if min_crop and min(h, w) < min_crop:
            continue
        if use_reid and min_sharp > 0:
            # 拉普拉斯方差过低说明模糊，喂给模型只会得到噪声特征
            try:
                gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
                if float(cv2.Laplacian(gray, cv2.CV_64F).var()) < min_sharp:
                    continue
            except Exception:
                pass
        crops.append(crop)
        if first_bbox is None:
            first_bbox = bbox
            first_frame_h = image.shape[0]

    if not crops or first_bbox is None:
        return None

    if use_reid:
        encoded = _reid_descriptor(crops, first_bbox, first_frame_h, cfg)
        if encoded is not None:
            return base64.b64encode(encoded).decode('ascii')
        # 模型不可用 → 静默退回直方图，保证功能不中断
    try:
        raw = _histogram_descriptor(crops[0], first_bbox, first_frame_h)
        return base64.b64encode(raw).decode('ascii')
    except Exception as e:
        logger.debug(f"[CrossCamera] 外观特征提取失败: {e}")
        return None


# ========================================
# 解码与比较
# ========================================

def decode_descriptor(encoded: str) -> Optional[np.ndarray]:
    """解码 v1 直方图描述子；格式不符返回 None（保留给旧调用方）"""
    parsed = decode_any(encoded)
    if parsed is None:
        return None
    version, vector, _meta, _mu = parsed
    return vector if version == DESCRIPTOR_VERSION else None


def decode_any(encoded):
    """解码任意版本描述子

    Returns:
        (version, vector, meta, mu_hash)
        v1 的 vector 是 uint8 数组、mu_hash 为 0；v2 的 vector 是 float32。
        meta 是 (aspect_q, height_q, v_upper, v_lower)
        无法解析返回 None
    """
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded.encode('ascii'))
    except Exception:
        return None
    if len(raw) < 4:
        return None

    # v2 先判 magic（v1 长度固定 148，不会误判）
    if raw[:2] == _MAGIC and raw[2] == REID_VERSION:
        if len(raw) < _V2_HEADER:
            return None
        dim = struct.unpack('<H', raw[4:6])[0]
        if dim <= 0 or len(raw) != _V2_HEADER + 2 * dim:
            return None
        vec = np.frombuffer(raw[_V2_HEADER:], dtype=np.float16).astype(np.float32)
        meta = tuple(raw[6:10])
        mu_hash = struct.unpack('<I', raw[10:14])[0]
        return REID_VERSION, vec, meta, mu_hash

    if len(raw) == _DESC_LEN:
        arr = np.frombuffer(raw, dtype=np.uint8)
        meta = tuple(int(v) for v in arr[144:148])
        return DESCRIPTOR_VERSION, arr, meta, 0

    return None


def _shape_similarity(meta_a, meta_b) -> float:
    """形状先验：宽高比 + 相对身高 + 亮度接近度"""
    ar_a, ar_b = meta_a[0] / 255.0 * 5.0, meta_b[0] / 255.0 * 5.0
    ar_sim = 1.0 - min(1.0, abs(ar_a - ar_b) / max(ar_a, ar_b, 1e-3))
    h_a, h_b = meta_a[1] / 255.0, meta_b[1] / 255.0
    h_sim = 1.0 - min(1.0, abs(h_a - h_b) / max(h_a, h_b, 1e-3))
    v_sim = 1.0 - abs((meta_a[2] + meta_a[3]) / 2.0 - (meta_b[2] + meta_b[3]) / 2.0) / 255.0
    return float(np.clip(0.45 * ar_sim + 0.35 * h_sim + 0.20 * v_sim, 0.0, 1.0))


def _compare_histogram(arr_a: np.ndarray, arr_b: np.ndarray) -> float:
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
    return float(np.clip(appearance / sum(_REGION_WEIGHTS), 0.0, 1.0))


def _compare_cosine(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(vec_a)), float(np.linalg.norm(vec_b))
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    cos = float(np.dot(vec_a, vec_b) / (na * nb))
    # 余弦范围 [-1, 1]，ReID 特征实际落在 [0, 1]，负值裁掉
    return float(np.clip(cos, 0.0, 1.0))


def compare(desc_a, desc_b) -> Dict[str, float]:
    """比较两个描述子的外观/形状相似度

    Args:
        desc_a/desc_b: extract_descriptor 的返回值（base64）或已解码数组

    Returns:
        {'appearance': 0~1, 'shape': 0~1, 'compatible': 0/1, 'version': 1|2}
        compatible=0 表示两端特征不可比（版本不同、均值不同、维度不同），
        调用方应视为"没有外观特征"。
    """
    parsed_a = decode_any(desc_a) if isinstance(desc_a, str) else None
    parsed_b = decode_any(desc_b) if isinstance(desc_b, str) else None
    if parsed_a is None or parsed_b is None:
        return {'appearance': 0.0, 'shape': 0.0, 'compatible': 0.0, 'version': 0}

    ver_a, vec_a, meta_a, mu_a = parsed_a
    ver_b, vec_b, meta_b, mu_b = parsed_b
    if ver_a != ver_b:
        return {'appearance': 0.0, 'shape': 0.0, 'compatible': 0.0, 'version': 0}
    if ver_a == REID_VERSION:
        # 维度或中心化均值不同 → 特征空间不同，不能比
        if vec_a.shape[0] != vec_b.shape[0] or int(mu_a) != int(mu_b):
            return {'appearance': 0.0, 'shape': 0.0, 'compatible': 0.0, 'version': 0}

    if ver_a == REID_VERSION:
        appearance = _compare_cosine(vec_a, vec_b)
    else:
        appearance = _compare_histogram(vec_a, vec_b)

    return {
        'appearance': float(np.clip(appearance, 0.0, 1.0)),
        'shape': _shape_similarity(meta_a, meta_b),
        'compatible': 1.0,
        'version': int(ver_a),
    }
