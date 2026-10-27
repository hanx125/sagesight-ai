"""
跨相机跟踪配置读写

单一真相源：config/cross_camera.yaml
- 进程内带 mtime 缓存，页面改完立即生效（无需重启）
- 归一化：缺字段回退默认值，类型错误不抛出（融合服务不能被配置打死）
"""

import copy
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CONFIG_PATH = _PROJECT_ROOT / 'config' / 'cross_camera.yaml'

# ========================================
# 默认配置（与 cross_camera.yaml 保持一致）
# ========================================
DEFAULTS: Dict[str, Any] = {
    'enabled': True,
    'poll_interval': 1.0,
    'batch_size': 100,
    'max_gap_seconds': 120,
    'max_overlap_seconds': 60,
    'identity_ttl_minutes': 30,
    'max_cameras_per_identity': 32,
    'groups': [],
    'class_families': {
        'person': ['人', '人员', '行人', '工人', '人体', 'person', 'pedestrian', 'people'],
        'vehicle': ['汽车', '车', '轿车', '卡车', '货车', '客车', '公交车',
                    '摩托车', '自行车', '三轮车', 'car', 'truck', 'bus',
                    'vehicle', 'motorbike', 'motorcycle', 'bicycle'],
    },
    'thresholds': {
        'person': {'min_score': 0.62, 'min_score_reid': 0.58, 'min_margin': 0.08,
                   'use_appearance': True, 'max_gap_seconds': 120,
                   'min_score_temporal_only': 0.35},
        'vehicle': {'min_score': 0.66, 'min_score_reid': 0.62, 'min_margin': 0.08,
                    'use_appearance': True, 'max_gap_seconds': 180,
                    'min_score_temporal_only': 0.40},
        'other': {'min_score': 0.74, 'min_score_reid': 0.70, 'min_margin': 0.10,
                  'use_appearance': True, 'max_gap_seconds': 60,
                  'min_score_temporal_only': 0.50},
    },
    'weights': {'appearance': 0.55, 'shape': 0.15, 'temporal': 0.30},
    'use_strong_id': True,
    'link_same_camera': False,
    'feature': {
        'backend': 'reid',
        'extract_in': 'fusion',
        'model_path': 'models/reid/reid_resnet18.onnx',
        'input_width': 128,
        'input_height': 256,
        'num_threads': 2,
        'min_crop_px': 24,
        'min_sharpness': 0.0,
    },
}

_cache: Dict[str, Any] = {}
_cache_mtime: float = -1.0
_lock = threading.RLock()

# 保存时写在文件头部的字段说明（yaml.safe_dump 不保留注释，重写后靠这段兜底）
_FILE_HEADER = """# ========================================
# 跨相机跟踪配置
# ========================================
# 本文件由「系统配置 → 跨相机跟踪」页面写入，保存时会整体重写（手写注释不保留）。
# 字段说明：
#   enabled                   总开关（关闭后融合服务不工作，事件不再提取外观特征）
#   poll_interval             轮询间隔（秒）
#   batch_size                单次轮询最多处理的事件数（防重启后积压）
#   max_gap_seconds           默认通行时间窗（秒）：A 消失到 B 出现的最长间隔
#   max_overlap_seconds       两相机同时看到同一目标的允许重叠（秒）
#   identity_ttl_minutes      身份活跃窗口（分钟），超时标记 closed 不再参与关联
#   max_cameras_per_identity  单个身份最多关联的相机数
#   groups[]                  区域分组 {name, cameras[], max_gap_seconds}；
#                             只有同组相机之间才允许关联，空列表 = 全部互通
#   class_families            类别族（中英文标签都收录），决定用哪套阈值
#   thresholds.<family>       min_score / min_score_temporal_only / min_margin /
#                             max_gap_seconds（family = person | vehicle | other）
#   weights                   appearance / shape / temporal 权重（保存时自动归一化）
#   use_strong_id             是否用 人脸姓名 / 车牌号 做硬关联
#   link_same_camera          是否允许同一相机上的两段轨迹互相关联（默认 false；
#                             只在同相机重入场景才有意义，开启容易把不同目标串成一条）
#   feature.backend           外观特征后端：reid（ONNX 模型，判别力强）/ histogram（颜色直方图，兜底）
#   feature.extract_in        提取时机：fusion（融合线程，单份模型、支持历史重算，默认）
#                             / worker（相机进程，内存按相机数翻倍，只在单机位场景考虑）
#   feature.model_path        ONNX 模型路径（相对项目根目录）
#   feature.input_width/height 模型输入尺寸，模型自带固定尺寸时以模型为准
#   feature.num_threads       onnxruntime CPU 线程数（盒子要给解码/检测留余量）
#   feature.min_crop_px       目标过小时跳过（像素），避免给小目标算噪声特征
#   feature.min_sharpness     模糊度下限（拉普拉斯方差），0 = 不检查
#   thresholds.<family>.min_score_reid  ReID 后端的综合分下限（与直方图的 min_score 分开调）
#   thresholds.<family>.same_camera_min_appearance 同相机重入的外观门槛，0 = 按版本默认
"""


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并（override 覆盖 base，保留 base 中缺失的键）"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _to_float(value, default: float, minimum: float = None, maximum: float = None) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and num < minimum:
        return minimum
    if maximum is not None and num > maximum:
        return maximum
    return num


def _normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把任意输入归一化成可用配置"""
    cfg = _deep_merge(DEFAULTS, raw or {})

    cfg['enabled'] = bool(cfg.get('enabled', True))
    cfg['poll_interval'] = _to_float(cfg.get('poll_interval'), 1.0, 0.2, 60.0)
    cfg['max_gap_seconds'] = _to_float(cfg.get('max_gap_seconds'), 120.0, 1.0, 3600.0)
    cfg['max_overlap_seconds'] = _to_float(cfg.get('max_overlap_seconds'), 60.0, 0.0, 600.0)
    cfg['max_cameras_per_identity'] = int(_to_float(
        cfg.get('max_cameras_per_identity'), 32, 1, 256))
    cfg['batch_size'] = int(_to_float(cfg.get('batch_size'), 100, 1, 1000))
    cfg['identity_ttl_minutes'] = _to_float(cfg.get('identity_ttl_minutes'), 30.0, 1.0, 1440.0)
    cfg['use_strong_id'] = bool(cfg.get('use_strong_id', True))
    cfg['link_same_camera'] = bool(cfg.get('link_same_camera', False))

    # 分组：过滤掉非法项，统一成 {name, cameras[], max_gap_seconds}
    groups: List[Dict[str, Any]] = []
    for idx, group in enumerate(cfg.get('groups') or []):
        if not isinstance(group, dict):
            continue
        cameras = [str(c).strip() for c in (group.get('cameras') or []) if str(c).strip()]
        if not cameras:
            continue
        groups.append({
            'name': str(group.get('name') or f'分组{idx + 1}'),
            'cameras': cameras,
            'max_gap_seconds': _to_float(
                group.get('max_gap_seconds'), cfg['max_gap_seconds'], 1.0, 3600.0),
        })
    cfg['groups'] = groups

    # 类别族：统一小写化，便于匹配
    families: Dict[str, List[str]] = {}
    for family, names in (cfg.get('class_families') or {}).items():
        if not isinstance(names, list):
            continue
        families[str(family)] = [str(n).strip().lower() for n in names if str(n).strip()]
    cfg['class_families'] = families

    # 阈值：补齐三族，数值兜底
    thresholds: Dict[str, Dict[str, Any]] = {}
    for family in ('person', 'vehicle', 'other'):
        src = (cfg.get('thresholds') or {}).get(family) or {}
        default = DEFAULTS['thresholds'][family]
        thresholds[family] = {
            'min_score': _to_float(src.get('min_score'), default['min_score'], 0.0, 1.0),
            'min_margin': _to_float(src.get('min_margin'), default['min_margin'], 0.0, 1.0),
            'use_appearance': bool(src.get('use_appearance', default['use_appearance'])),
            'max_gap_seconds': _to_float(
                src.get('max_gap_seconds'), default['max_gap_seconds'], 1.0, 3600.0),
            # 没有外观特征时（快照缺失）的独立阈值：纯时空关联更依赖时间接近
            'min_score_temporal_only': _to_float(
                src.get('min_score_temporal_only'),
                default.get('min_score_temporal_only', 0.35), 0.0, 1.0),
            # ReID 后端的综合分下限：余弦分布与直方图交集不同，需要独立阈值
            'min_score_reid': _to_float(
                src.get('min_score_reid'), default.get('min_score_reid', 0.62), 0.0, 1.0),
            # 同相机重入的外观门槛（ReID 下应更高），未配置时由匹配器按版本给默认
            'same_camera_min_appearance': _to_float(
                src.get('same_camera_min_appearance'), 0.0, 0.0, 1.0),
        }
    cfg['thresholds'] = thresholds

    weights = cfg.get('weights') or {}
    cfg['weights'] = {
        'appearance': _to_float(weights.get('appearance'), 0.55, 0.0, 1.0),
        'shape': _to_float(weights.get('shape'), 0.15, 0.0, 1.0),
        'temporal': _to_float(weights.get('temporal'), 0.30, 0.0, 1.0),
    }
    total = sum(cfg['weights'].values())
    if total <= 0:
        cfg['weights'] = dict(DEFAULTS['weights'])
    elif abs(total - 1.0) > 1e-6:
        # 归一化，避免线上手改权重把分数算歪
        cfg['weights'] = {k: v / total for k, v in cfg['weights'].items()}

    # 外观特征：后端与提取时机
    feat = cfg.get('feature') or {}
    feat_default = DEFAULTS['feature']
    backend = str(feat.get('backend') or feat_default['backend']).strip().lower()
    if backend not in ('reid', 'histogram'):
        backend = feat_default['backend']
    extract_in = str(feat.get('extract_in') or feat_default['extract_in']).strip().lower()
    if extract_in not in ('fusion', 'worker'):
        extract_in = feat_default['extract_in']
    cfg['feature'] = {
        'backend': backend,
        'extract_in': extract_in,
        'model_path': str(feat.get('model_path') or feat_default['model_path']).strip(),
        'input_width': int(_to_float(feat.get('input_width'), 128, 32, 512)),
        'input_height': int(_to_float(feat.get('input_height'), 256, 32, 1024)),
        'num_threads': int(_to_float(feat.get('num_threads'), 2, 1, 16)),
        'min_crop_px': int(_to_float(feat.get('min_crop_px'), 24, 0, 256)),
        'min_sharpness': _to_float(feat.get('min_sharpness'), 0.0, 0.0, 500.0),
    }

    return cfg


def reload_cross_camera_config() -> Dict[str, Any]:
    """强制重新读盘"""
    global _cache, _cache_mtime
    with _lock:
        raw: Dict[str, Any] = {}
        mtime = -1.0
        if CONFIG_PATH.exists():
            try:
                mtime = os.path.getmtime(CONFIG_PATH)
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    raw = (yaml.safe_load(f) or {}).get('cross_camera', {}) or {}
            except Exception as e:
                logger.warning(f"[CrossCamera] 读取配置失败，使用默认值: {e}")
                raw = {}
        _cache = _normalize(raw)
        _cache_mtime = mtime
        return _cache


def get_cross_camera_config() -> Dict[str, Any]:
    """获取配置（按文件 mtime 自动热更新）"""
    global _cache
    with _lock:
        if _cache:
            try:
                mtime = os.path.getmtime(CONFIG_PATH) if CONFIG_PATH.exists() else -1.0
            except OSError:
                mtime = -1.0
            if mtime == _cache_mtime:
                return _cache
        return reload_cross_camera_config()


def is_cross_camera_enabled() -> bool:
    """总开关（读配置失败时返回 False，宁可不做也不误关联）"""
    try:
        return bool(get_cross_camera_config().get('enabled'))
    except Exception:
        return False


def save_cross_camera_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """保存配置（只覆盖 cross_camera 段，保留文件注释外的其它段）

    用 partial update：调用方只传需要改的键，其余保持原值。
    写入前先做归一化，避免把非法值落盘。
    """
    with _lock:
        current = get_cross_camera_config()
        merged = _deep_merge(current, updates or {})
        normalized = _normalize(merged)

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {'cross_camera': normalized}
        tmp_path = CONFIG_PATH.with_suffix('.yaml.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(_FILE_HEADER)
            yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False,
                           default_flow_style=False)
        os.replace(tmp_path, CONFIG_PATH)

        global _cache, _cache_mtime
        _cache = normalized
        try:
            _cache_mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            _cache_mtime = -1.0
        logger.info("[CrossCamera] 配置已保存")
        return normalized
