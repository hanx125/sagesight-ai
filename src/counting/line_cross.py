"""
人员统计 - 分界线跨越方向判定（模式A：单机画线）

核心思路：
- 使用目标 bbox 底边中心点（脚步点）与分界线的侧向关系判断进出
- 侧向变化 + 最小位移阈值进入惯性确认，连续 N 帧后判定跨越（去抖）
- 同一跟踪目标同日内只计一次某方向，防止反复横跳重复计数

坐标系：cross_line 为归一化坐标（范围 0-1），与帧内 bbox（绝对像素）换算
时需先把 bbox 归一化。bbox 采用 [x1, y1, x2, y2] 像素坐标。
"""
import math
from typing import Callable, Dict, Optional

import logging

logger = logging.getLogger(__name__)


def normalize_bbox(bbox, frame_shape):
    """把像素 bbox [x1,y1,x2,y2] 归一化到 [0,1]（frame_shape = (height, width)）"""
    h, w = frame_shape[0], frame_shape[1]
    if w <= 0 or h <= 0:
        return bbox
    x1, y1, x2, y2 = bbox
    return [x1 / w, y1 / h, x2 / w, y2 / h]


def foot_point(bbox_norm):
    """归一化 bbox 的底边中心点（脚步点），与 zone_manager 判点方式一致"""
    return [(bbox_norm[0] + bbox_norm[2]) / 2.0, bbox_norm[3]]


def side_of_point(p1, p2, foot):
    """判断点在线段（p1→p2）的哪一侧

    返回 +1 或 -1：叉积 (p2-p1)×(foot-p1) 的符号。
    叉积 > 0 记 +1，<= 0 记 -1（退化/共线归为 -1 侧）。
    """
    cross = (p2[0] - p1[0]) * (foot[1] - p1[1]) - (p2[1] - p1[1]) * (foot[0] - p1[0])
    return 1 if cross > 0 else -1


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class LineCrossTracker:
    """单个分界线的跨越跟踪器（每相机每区域一个实例）

    - tracks: track_id → 轨迹状态（last_foot, last_side, pending_side, pending_count, counted_today）
    - 每个跨越回调由 on_cross(direction) 触发（direction: 'in' | 'out'）
    - 按天复位：reset_day() 清空当日计数标志（tracks 轨迹保留以便继续跟踪）
    """

    def __init__(self, zone, frame_shape, on_cross: Callable[[str], None]):
        self.zone = zone
        self.frame_shape = frame_shape
        self.on_cross = on_cross
        self.inertia = max(1, int(zone.inertia))
        self.min_displacement = max(0.0, float(zone.min_displacement))
        # in_side=1 表示叉积>0一侧为进；in_side=-1 表示叉积<0一侧为进
        self.in_side = 1 if int(zone.in_side) >= 0 else -1
        self.tracks: Dict[str, dict] = {}

    def reset_day(self):
        """按天复位：清空当日计数标志，保留轨迹状态继续跟踪"""
        for st in self.tracks.values():
            st['counted_in'] = False
            st['counted_out'] = False

    def update(self, track_id, bbox, frame_time):
        """逐帧更新单个跟踪目标的跨越状态

        Args:
            track_id: 跟踪目标ID
            bbox: 像素 bbox [x1,y1,x2,y2]
            frame_time: 帧时间戳
        """
        zone = self.zone
        if not zone.cross_line:
            return
        bbox_norm = normalize_bbox(bbox, self.frame_shape)
        foot = foot_point(bbox_norm)
        p1, p2 = zone.cross_line[0], zone.cross_line[1]
        side = side_of_point(p1, p2, foot)

        st = self.tracks.get(track_id)
        if st is None:
            self.tracks[track_id] = {
                'last_foot': foot,
                'last_side': side,
                'pending_side': None,
                'pending_count': 0,
                'counted_in': False,
                'counted_out': False,
            }
            return

        disp = _distance(foot, st['last_foot'])

        # 未跨侧：保持稳定侧，更新最近位置，回退惯性计数
        if side == st['last_side']:
            st['last_foot'] = foot
            st['last_side'] = side
            st['pending_count'] = 0
            st['pending_side'] = None
            return

        # 已跨侧但位移不足：视为抖动，保持稳定侧，不更新 last_foot（位置基准不变）
        if disp < self.min_displacement:
            st['pending_count'] = 0
            st['pending_side'] = None
            return

        # 已跨侧且位移足够：进入惯性确认
        if side != st['pending_side']:
            # 首次跨侧：开始确认（last_foot/last_side 保持跨越前基准）
            st['pending_side'] = side
            st['pending_count'] = 1
        else:
            st['pending_count'] += 1
            if st['pending_count'] >= self.inertia:
                # 惯性确认通过：判定跨越（进/出按方向分别抑制同方向重复）
                direction = 'in' if side == self.in_side else 'out'
                flag = 'counted_in' if direction == 'in' else 'counted_out'
                if not st[flag]:
                    st[flag] = True
                    try:
                        self.on_cross(direction)
                    except Exception as e:
                        logger.error(f"[LineCross] 跨越回调失败: {e}")
                st['last_foot'] = foot
                st['last_side'] = side
                st['pending_count'] = 0
                st['pending_side'] = None