"""
区域状态机与通道级聚合

状态定义:
- UNCALIBRATED  无基线，等待人工拍摄；不判定不告警
- MONITORING    监测中；差异持续达 dwell 阈值 → ALERTED；全局突变 → INVALID
- ALERTED       已告警；创建事件后不重复告警；差异持续低于最小阈值 clear_confirm 秒 → 恢复
- INVALID       基线失效（全局突变，如昼夜切换/相机位移）；不告警，等待重拍

参数默认值（区域级可覆盖 dwell / min_area_ratio）:
- dwell_seconds:        pile_up 300s / abnormal_occupancy 60s
- grace_seconds:        5   候选期间差异短暂跌落的容忍窗口，不重置计时
- clear_confirm_seconds: 30 差异持续低于最小阈值该时长才判定"已移走"
- min_area_ratio:       0.02
- global_change_ratio:  0.60（全区域最大占比超限 → 全局突变）
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.occupancy.baseline_manager import BaselineManager
from src.occupancy.diff_analyzer import DiffAnalyzer

logger = logging.getLogger(__name__)

# 全局突变判定线：任一区域差异占比超限视为光照切换/相机位移
GLOBAL_CHANGE_RATIO = 0.60

# 候选期间差异短暂跌落到阈值下的容忍窗口（秒）
GRACE_SECONDS = 5

# 差异持续低于最小阈值该时长才判定"已移走"（秒）
CLEAR_CONFIRM_SECONDS = 30

# 最小面积占比默认值（区域级 min_area_ratio 缺省时）
DEFAULT_MIN_AREA_RATIO = 0.02

# 分类型默认持续时长（秒）：堆积形成慢允许较长确认；异常占用需较快响应
DEFAULT_DWELL = {'pile_up': 300, 'abnormal_occupancy': 60}

# 分类型区域启用开关 / 区域级 dwell 覆盖字段
ZONE_ENABLE_KEY = {'pile_up': 'enable_pileup', 'abnormal_occupancy': 'enable_occupancy'}
ZONE_DWELL_KEY = {'pile_up': 'pileup_dwell_seconds', 'abnormal_occupancy': 'occupancy_dwell_seconds'}


class ZoneOccupancyState:
    """单区域状态机"""

    def __init__(
        self,
        zone_name: str,
        dwell_seconds: int,
        min_area_ratio: float = DEFAULT_MIN_AREA_RATIO,
        grace_seconds: float = GRACE_SECONDS,
        clear_confirm_seconds: float = CLEAR_CONFIRM_SECONDS,
    ):
        self.zone_name = zone_name
        self.dwell_seconds = dwell_seconds
        self.min_area_ratio = min_area_ratio
        self.grace_seconds = grace_seconds
        self.clear_confirm_seconds = clear_confirm_seconds

        self._state = 'UNCALIBRATED'
        # 候选起始时间（MONITORING 下差异持续超阈值起算）
        self._candidate_start: Optional[float] = None
        # 候选期间首次跌破阈值的时间（grace 窗口起点），恢复超限则清除
        self._low_since: Optional[float] = None
        # ALERTED 期间首次跌破阈值的时间（clear_confirm 起算）
        self._clear_since: Optional[float] = None
        self.last_alert_at: Optional[float] = None
        # 全局突变提示只发一次（重新校准后复位）
        self._invalid_notified = False

    # ----------------------------------------
    # 状态迁移
    # ----------------------------------------

    def update(self, ratio: float, now: float) -> Optional[str]:
        """输入本秒面积占比，返回状态迁移: 'alert' / 'recovery' / None"""
        if self._state in ('UNCALIBRATED', 'INVALID'):
            return None

        if self._state == 'MONITORING':
            return self._update_monitoring(ratio, now)
        return self._update_alerted(ratio, now)

    def _update_monitoring(self, ratio: float, now: float) -> Optional[str]:
        over = ratio >= self.min_area_ratio

        if over:
            # 差异回到阈值上：清除 grace 计时
            self._low_since = None
            if self._candidate_start is None:
                self._candidate_start = now
            elif now - self._candidate_start >= self.dwell_seconds:
                self._state = 'ALERTED'
                self.last_alert_at = now
                return 'alert'
        else:
            # 跌破阈值：grace 窗口内保留候选计时，超过窗口重置
            if self._candidate_start is not None:
                if self._low_since is None:
                    self._low_since = now
                elif now - self._low_since > self.grace_seconds:
                    self._candidate_start = None
                    self._low_since = None
        return None

    def _update_alerted(self, ratio: float, now: float) -> Optional[str]:
        if ratio >= self.min_area_ratio:
            # 仍有差异：重置恢复计时，不重复告警
            self._clear_since = None
            return None

        # 差异低于最小阈值：持续 clear_confirm 秒后判定"已移走"
        if self._clear_since is None:
            self._clear_since = now
        elif now - self._clear_since >= self.clear_confirm_seconds:
            self._state = 'MONITORING'
            self._clear_since = None
            self._candidate_start = None
            return 'recovery'
        return None

    def invalidate(self, now: float) -> Optional[str]:
        """全局突变 → INVALID；仅从 MONITORING 迁移时返回一次提示"""
        if self._state == 'MONITORING':
            self._state = 'INVALID'
            self._candidate_start = None
            self._low_since = None
            if not self._invalid_notified:
                self._invalid_notified = True
                return 'global_change'
        return None

    def recalibrate(self):
        """拍摄新基线后重新进入监测（告警状态一并复位，由人工干预收尾）"""
        self._state = 'MONITORING'
        self._candidate_start = None
        self._low_since = None
        self._clear_since = None
        self._invalid_notified = False

    @property
    def state(self) -> str:
        return self._state

    def snapshot(self) -> dict:
        """状态摘要（供状态回写与前端展示）"""
        return {
            'name': self.zone_name,
            'state': self._state,
            'last_alert_at': self.last_alert_at,
        }


class ChannelOccupancyMonitor:
    """通道级聚合：按检测类型筛选启用区域，持有每区域状态机与差分分析器"""

    def __init__(
        self,
        channel_id,
        detection_type: str,
        zones_config: List[dict],
        frame_shape: Tuple[int, int],
    ):
        """
        Args:
            channel_id: 通道 ID（基线按通道存储）
            detection_type: 'pile_up' 或 'abnormal_occupancy'
            zones_config: 通道级区域配置列表
            frame_shape: 帧形状 (height, width)
        """
        self.channel_id = channel_id
        self.detection_type = detection_type
        self.frame_shape = frame_shape

        self.analyzer = DiffAnalyzer()
        self.baseline_y = None

        # 筛选本检测类型启用的区域，构建多边形（像素坐标）与状态机
        enable_key = ZONE_ENABLE_KEY.get(detection_type, '')
        dwell_key = ZONE_DWELL_KEY.get(detection_type, '')
        frame_h, frame_w = frame_shape

        self.zones: List[dict] = []  # [{name, polygon_px, state}]
        for zone in (zones_config or []):
            if not isinstance(zone, dict) or not zone.get(enable_key):
                continue
            polygon = zone.get('polygon') or []
            if len(polygon) < 3:
                continue
            polygon_px = [
                (float(p[0]) * frame_w, float(p[1]) * frame_h)
                for p in polygon
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
            if len(polygon_px) < 3:
                continue
            dwell = zone.get(dwell_key) or DEFAULT_DWELL.get(detection_type, 60)
            min_ratio = zone.get('min_area_ratio') or DEFAULT_MIN_AREA_RATIO
            self.zones.append({
                'name': zone.get('name', f'zone_{len(self.zones)}'),
                'polygon_px': polygon_px,
                'state': ZoneOccupancyState(
                    zone_name=zone.get('name', f'zone_{len(self.zones)}'),
                    dwell_seconds=int(dwell),
                    min_area_ratio=float(min_ratio),
                ),
            })

    # ----------------------------------------
    # 基线
    # ----------------------------------------

    def load_baseline(self) -> bool:
        """从磁盘加载基线（任务启动时调用）；无启用区域或无基线返回 False"""
        if not self.zones:
            return False
        baseline = BaselineManager.load(self.channel_id)
        if baseline is None:
            return False
        frame_h, frame_w = self.frame_shape
        if baseline.shape[0] != frame_h or baseline.shape[1] != frame_w:
            logger.warning(
                f"基线尺寸与帧不一致 channel={self.channel_id}: "
                f"baseline={baseline.shape[:2]}, frame=({frame_h},{frame_w})，需重拍基线"
            )
            return False
        self.baseline_y = baseline
        for z in self.zones:
            if z['state'].state == 'UNCALIBRATED':
                z['state'].recalibrate()
        return True

    def handle_baseline_captured(self, baseline_y: np.ndarray, now: float):
        """拍摄新基线后注入并复位所有区域状态"""
        self.baseline_y = baseline_y
        for z in self.zones:
            z['state'].recalibrate()

    # ----------------------------------------
    # 逐帧处理
    # ----------------------------------------

    def process_frame(self, y_plane, now: float) -> List[dict]:
        """处理一帧，返回本帧产生的状态迁移消息

        消息格式: {event, zone_name, area_ratio, dwell_seconds, diff_bbox, timestamp}
        - event: 'alert' / 'recovery' / 'global_change'
        - global_change 为通道级消息（zone_name 取触发迁移的区域）
        """
        if not self.zones or self.baseline_y is None:
            return []

        messages: List[dict] = []

        # 逐区域差分
        results = []
        for z in self.zones:
            diff = self.analyzer.analyze(y_plane, self.baseline_y, z['polygon_px'])
            results.append((z, diff))

        # 全局突变判定：任一区域超限 → 光照切换/相机位移，不告警
        max_ratio = max(d.area_ratio for _, d in results)
        if max_ratio >= GLOBAL_CHANGE_RATIO:
            for z, _ in results:
                event = z['state'].invalidate(now)
                if event == 'global_change':
                    messages.append({
                        'event': 'global_change',
                        'zone_name': z['name'],
                        'area_ratio': round(max_ratio, 4),
                        'dwell_seconds': 0,
                        'diff_bbox': None,
                        'timestamp': now,
                    })
            return messages

        # 正常判定：逐区域状态机迁移
        for z, diff in results:
            transition = z['state'].update(diff.area_ratio, now)
            if transition is None:
                continue
            messages.append({
                'event': transition,
                'zone_name': z['name'],
                'area_ratio': round(diff.area_ratio, 4),
                'dwell_seconds': z['state'].dwell_seconds if transition == 'alert' else 0,
                'diff_bbox': diff.bbox if transition == 'alert' else None,
                'timestamp': now,
            })
        return messages

    # ----------------------------------------
    # 状态摘要
    # ----------------------------------------

    def status_summary(self) -> dict:
        """整体状态: 任一区域失效 > 已告警 > 未校准 > 监测中"""
        states = [z['state'].state for z in self.zones]
        if 'INVALID' in states:
            overall = 'invalid'
        elif 'ALERTED' in states:
            overall = 'alerted'
        elif 'UNCALIBRATED' in states or self.baseline_y is None:
            overall = 'uncalibrated'
        else:
            overall = 'monitoring'
        return {
            'state': overall,
            'zones': [z['state'].snapshot() for z in self.zones],
        }
