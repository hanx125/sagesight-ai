"""
跨相机关联匹配

输入都是"已结束的轨迹片段"（一条事件），所以这里不需要考虑实时性，
只要把"同一个人"判对。判定顺序刻意做成硬门控优先：

    同类别（必须） → 强 ID（人脸/车牌）→ 相机拓扑（同组 / 同相机开关）
    → 通行时间窗（必须）→ 外观 + 形状 + 时间 加权打分 → 最高分且领先第二名足够多

硬门控不放宽的原因：错关联比不关联更糟——一条轨迹里混进别人，
后面所有基于轨迹的判断都会错。
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.tracking.cross_camera import features as feat_mod

logger = logging.getLogger(__name__)


# ========================================
# 数据结构
# ========================================

@dataclass
class Tracklet:
    """一条轨迹片段（对应一条已落库事件）"""
    event_id: str
    camera_id: str
    class_name: str
    start_time: float                 # epoch 秒
    end_time: float                   # epoch 秒
    feature: Optional[str] = None     # 外观描述子（base64）
    strong_id: Optional[str] = None   # 人脸姓名 / 车牌号
    image_url: str = ''
    confidence: float = 0.0
    task_id: Optional[int] = None
    zones: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


@dataclass
class MatchResult:
    """一条候选的评估结果"""
    candidate: Any
    score: float
    reason: str
    components: Dict[str, float] = field(default_factory=dict)


# ========================================
# 时间与强 ID 解析
# ========================================

def parse_event_time(value) -> float:
    """把事件时间统一转成 epoch 秒

    事件表里 start_time/end_time 是 DATETIME 字符串（如
    '2026-10-27 10:27:48.950618'），也可能已经是 datetime 或数字。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    text = str(value).strip()
    if not text:
        return 0.0
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def strong_id_from_metadata(metadata) -> Optional[str]:
    """从事件 metadata 提取强 ID（人脸姓名 / 车牌号）

    人脸比车牌更可靠，同时存在时优先人脸。
    """
    if not isinstance(metadata, dict):
        return None
    face = metadata.get('face_match') or {}
    if isinstance(face, dict):
        name = (face.get('person_name') or '').strip()
        if name:
            return f"face:{name}"
    plate = metadata.get('plate_match') or {}
    if isinstance(plate, dict):
        no = (plate.get('plate_no') or '').strip()
        if no:
            return f"plate:{no}"
    return None


# ========================================
# 匹配器
# ========================================

class CrossCameraMatcher:
    """按配置做候选打分与决策（无状态，可跨轮复用）"""

    def __init__(self, cfg: Dict[str, Any]):
        self.update_config(cfg)

    def update_config(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg or {}
        self._families: Dict[str, List[str]] = self.cfg.get('class_families') or {}
        self._thresholds: Dict[str, Dict] = self.cfg.get('thresholds') or {}
        self._weights: Dict[str, float] = self.cfg.get('weights') or {}
        self._default_gap = float(self.cfg.get('max_gap_seconds') or 120.0)
        self._max_overlap = float(self.cfg.get('max_overlap_seconds') or 60.0)
        self._use_strong_id = bool(self.cfg.get('use_strong_id', True))
        # 同一相机上的两段轨迹默认不关联：那大概率是两个不同目标，
        # 一旦串起来会让身份越滚越大（实测同相机 37s 内的两辆车外观相似度 0.94）。
        self._link_same_camera = bool(self.cfg.get('link_same_camera', False))
        # camera_id -> 组名 / 组时间窗
        self._camera_group: Dict[str, str] = {}
        self._group_gap: Dict[str, float] = {}
        for group in self.cfg.get('groups') or []:
            name = group.get('name') or ''
            gap = float(group.get('max_gap_seconds') or self._default_gap)
            for camera_id in group.get('cameras') or []:
                self._camera_group[str(camera_id)] = name
                self._group_gap[name] = gap

    # ---------- 类别 / 分组 ----------

    def family_of(self, class_name: str) -> str:
        """类别 → 类别族（person / vehicle / other）"""
        name = (class_name or '').strip().lower()
        if not name:
            return 'other'
        for family, names in self._families.items():
            if family == 'other':
                continue
            if name in names:
                return family
        # 未精确命中时做包含匹配（标签常带前后缀，如 "person_0"）
        for family, names in self._families.items():
            if family == 'other':
                continue
            for candidate in names:
                if candidate and (candidate in name or name in candidate):
                    return family
        return 'other'

    def group_of(self, camera_id: str) -> Optional[str]:
        """相机所属区域组；未分组返回 None"""
        return self._camera_group.get(str(camera_id))

    def same_group(self, cam_a: str, cam_b: str) -> bool:
        """两台相机是否允许关联

        未配置任何分组时视为「所有相机同一组」，保证零配置也能用。
        """
        if not self._camera_group:
            return True
        ga, gb = self.group_of(cam_a), self.group_of(cam_b)
        if ga is None or gb is None:
            # 有分组配置时，未登记的相机只在彼此都未登记时才允许互相关联
            return ga is None and gb is None
        return ga == gb

    def max_gap(self, family: str, camera_a: str, camera_b: str) -> float:
        """允许的通行时间窗（族阈值 与 组时间窗 取小值）"""
        family_gap = float(
            (self._thresholds.get(family) or {}).get('max_gap_seconds') or self._default_gap
        )
        group_name = self.group_of(camera_b) or self.group_of(camera_a)
        group_gap = self._group_gap.get(group_name, self._default_gap) if group_name else self._default_gap
        return min(family_gap, group_gap)

    @property
    def link_same_camera(self) -> bool:
        """是否允许同相机互相关联（默认关闭）"""
        return self._link_same_camera

    def threshold_of(self, family: str) -> Dict[str, Any]:
        return self._thresholds.get(family) or self._thresholds.get('other') or {
            'min_score': 0.74, 'min_margin': 0.10,
            'use_appearance': True, 'max_gap_seconds': self._default_gap,
        }

    # ---------- 单个候选评估 ----------

    def evaluate(self, anchor: Tracklet, candidate: Tracklet) -> Optional[MatchResult]:
        """评估候选轨迹能否与锚点归为同一目标

        Args:
            anchor: 身份最近一次轨迹片段
            candidate: 待判定轨迹片段（新事件）

        Returns:
            MatchResult；被判为不可能时返回 None
        """
        # 1) 同类别（硬门控）：人和车永远不会关联
        if (anchor.class_name or '').strip().lower() != (candidate.class_name or '').strip().lower():
            return None

        family = self.family_of(candidate.class_name)
        threshold = self.threshold_of(family)

        # 2) 强 ID：人脸/车牌相同直接合并；不同直接拒绝
        if self._use_strong_id and anchor.strong_id and candidate.strong_id:
            if anchor.strong_id == candidate.strong_id:
                return MatchResult(candidate, 1.0, f'强ID一致({anchor.strong_id})',
                                   {'strong_id': 1.0})
            return None

        # 3) 相机拓扑（硬门控）
        same_camera = str(anchor.camera_id) == str(candidate.camera_id)
        if same_camera:
            # 强 ID 一致的情况已在第 2 步直接放行，这里只剩下"靠外观/时间猜"的路径。
            # 跨相机跟踪的语义是"同一个目标换了相机"，同相机重入交给单相机跟踪处理，
            # 默认关闭，避免把同机位的不同目标串成一条大轨迹。
            if not self._link_same_camera:
                return None
        elif not self.same_group(anchor.camera_id, candidate.camera_id):
            return None

        # 4) 通行时间窗（硬门控）
        gap = candidate.start_time - anchor.end_time
        max_gap = self.max_gap(family, anchor.camera_id, candidate.camera_id)
        if same_camera:
            # 同一相机上的两条轨迹必须时间不重叠，否则一定是两个人
            if gap < 2.0:
                return None
        else:
            # 跨相机允许重叠（有共同覆盖区域），但不能倒流太远
            if gap < -self._max_overlap:
                return None
        if gap > max_gap:
            return None

        temporal = 1.0 if gap <= 0 else max(0.0, 1.0 - gap / max_gap)

        # 5) 外观 + 形状
        use_appearance = bool(threshold.get('use_appearance', True))
        desc_a, desc_b = anchor.feature, candidate.feature
        sim = {'appearance': 0.0, 'shape': 0.0}
        has_appearance = use_appearance and bool(desc_a) and bool(desc_b)
        if has_appearance:
            sim = feat_mod.compare(desc_a, desc_b)

        weights = dict(self._weights or {'appearance': 0.55, 'shape': 0.15, 'temporal': 0.30})
        if not has_appearance:
            # 没有外观特征时（快照缺失/关掉外观），退化为纯时空关联：
            # 阈值也换成独立的 min_score_temporal_only，避免统一的 min_score
            # 把「时间很近但没有外观」的合理关联全部拒掉。
            weights.pop('appearance', None)
            weights.pop('shape', None)
            weights = {'temporal': 1.0}
        else:
            # 同一相机重入：同款衣服常见，外观要求更高
            if same_camera and sim['appearance'] < 0.72:
                return None

        total_weight = sum(weights.values()) or 1.0
        score = (
            weights.get('appearance', 0.0) * sim['appearance']
            + weights.get('shape', 0.0) * sim['shape']
            + weights.get('temporal', 0.0) * temporal
        ) / total_weight

        components = {
            'appearance': round(sim['appearance'], 4),
            'shape': round(sim['shape'], 4),
            'temporal': round(temporal, 4),
            'gap_seconds': round(gap, 2),
        }

        if has_appearance:
            required = float(threshold.get('min_score', 0.7))
        else:
            required = float(threshold.get(
                'min_score_temporal_only',
                0.35 if family == 'person' else 0.45))
        if score < required:
            return None

        parts = []
        if has_appearance:
            parts.append(f"外观{sim['appearance']:.2f}")
            parts.append(f"形状{sim['shape']:.2f}")
        parts.append(f"时间{temporal:.2f}（间隔{gap:.0f}s）")
        return MatchResult(candidate, score, ' '.join(parts), components)

    # ---------- 候选集决策 ----------

    def choose(self, anchor: Tracklet,
               candidates: List[Tracklet]) -> Tuple[Optional[MatchResult], Optional[MatchResult]]:
        """在一组候选中选出最好的一个

        Returns:
            (最佳结果或 None, 第二名结果或 None)
        """
        results: List[MatchResult] = []
        for candidate in candidates:
            if candidate.event_id == anchor.event_id:
                continue
            try:
                result = self.evaluate(anchor, candidate)
            except Exception as e:
                logger.debug(f"[CrossCamera] 候选评估异常 event={candidate.event_id[:8]}: {e}")
                continue
            if result is not None:
                results.append(result)

        if not results:
            return None, None

        results.sort(key=lambda r: r.score, reverse=True)
        best = results[0]
        second = results[1] if len(results) > 1 else None

        family = self.family_of(anchor.class_name)
        threshold = self.threshold_of(family)
        min_margin = float(threshold.get('min_margin', 0.08))

        # 强 ID 命中不受 margin 约束（这是确定性证据）
        if best.components.get('strong_id'):
            return best, second

        if second is not None and (best.score - second.score) < min_margin:
            logger.debug(
                f"[CrossCamera] 候选过于接近，放弃关联: best={best.score:.3f} "
                f"second={second.score:.3f} margin<{min_margin}"
            )
            return None, best
        return best, second
