"""
人员统计 - 每相机计数协调器

CountingManager 负责一个相机在其参与的所有计数区域上的计数：
  - mode='line'      用 LineCrossTracker 判定分界线跨越方向，跨越一次计一次
  - mode='direction' 按该相机在区域内的角色（入口机+1 / 出口机-1）计首次出现的跟踪目标

区域角色（line/in/out）由上层在注入 counting_zones 时按相机分辨率填充到
zone.role。按天复位：检测日期切换时清空当日内存态（跨越计数标志、方向去重集合）。
"""
from typing import Callable, Dict, List, Optional

from src.counting.zone_config import CountingZone, ROLE_LINE, ROLE_IN, ROLE_OUT
from src.counting.line_cross import LineCrossTracker
from src.counting.counting_stats import CountingStatsStore

import logging

logger = logging.getLogger(__name__)


class CountingManager:
    """某个相机的人员计数协调器"""

    def __init__(
        self,
        camera_id: str,
        counting_zones: List[CountingZone],
        stats_store: CountingStatsStore,
        frame_shape,
        emit: Callable[[Dict], None],
    ):
        """
        Args:
            camera_id: 相机ID（流标识）
            counting_zones: 该相机参与、且已解析好 role 的计数区域列表
            stats_store: 计数状态存储（跨事件进程共享单例）
            frame_shape: 帧尺寸 (height, width)，用于 bbox 归一化
            emit: 事件回调，接收 payload 字典
        """
        self.camera_id = camera_id
        self.stats_store = stats_store
        self.frame_shape = frame_shape
        self.emit = emit

        # 按模式分区
        self.line_trackers: List[LineCrossTracker] = []
        self.direction_zones: List[CountingZone] = []
        for zone in counting_zones:
            if zone.is_line():
                tracker = LineCrossTracker(
                    zone=zone,
                    frame_shape=frame_shape,
                    on_cross=lambda direction, z=zone: self._on_line_cross(z, direction),
                )
                self.line_trackers.append(tracker)
            else:
                self.direction_zones.append(zone)

        # 方向模式去重：zone_name → set(track_id)，同一人一日只计一次
        self._counted_tracks: Dict[str, set] = {
            z.zone_name: set() for z in self.direction_zones
        }
        self._current_date = stats_store.today()

        logger.debug(
            f"[CountingManager] 创建: camera_id={camera_id}, "
            f"line_zones={len(self.line_trackers)}, direction_zones={len(self.direction_zones)}"
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def step(self, frame_time, tracked_objects_dict: Optional[Dict]):
        """每帧驱动计数（在 TrackedObjectProcessor 的 _process_detection_data 中调用）"""
        self._ensure_day()
        if not tracked_objects_dict:
            return

        persons = []
        for track_id, obj in tracked_objects_dict.items():
            box = obj.get('box') if isinstance(obj, dict) else None
            if not box:
                continue
            label = obj.get('label', '') if isinstance(obj, dict) else ''
            # 仅统计配置目标类（默认人）；空配置视为统计全部目标
            persons.append((track_id, label, box))

        # 模式A：分界线跨越
        for tracker in self.line_trackers:
            for track_id, _label, box in persons:
                tracker.update(track_id, box, frame_time)

        # 模式B：进出方向汇总（首次出现的跟踪目标按角色计数）
        for zone in self.direction_zones:
            counted = self._counted_tracks.get(zone.zone_name, set())
            for track_id, _label, box in persons:
                if track_id in counted:
                    continue
                counted.add(track_id)
                direction = zone.role  # 'in' | 'out'
                self._apply_count(zone, zone.zone_name, zone.channel_id, direction)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _on_line_cross(self, zone: CountingZone, direction: str):
        """分界线跨越回调：更新计数并发事件"""
        self._apply_count(zone, zone.zone_name, zone.channel_id, direction)

    def _apply_count(self, zone: CountingZone, zone_name: str, channel_id, direction: str):
        """计数 +1 并触发事件（复用判断失败时仍应计数，channel_id 为 None 用 0 占位）"""
        cid = int(channel_id) if channel_id else 0
        try:
            store = self.stats_store
            store.increment(zone.task_id or 0, zone_name, cid, direction)
            summary = store.get_zone_summary(zone.task_id or 0, zone_name)
            task_totals = store.get_task_total(zone.task_id or 0)
            payload = {
                'camera_id': self.camera_id,
                'task_id': zone.task_id or 0,
                'zone_name': zone_name,
                'channel_id': cid,
                'direction': direction,
                'in_count': summary['in_count'],
                'out_count': summary['out_count'],
                'current_count': summary['current_count'],
                'task_total': task_totals['task_total'],
                'date': summary['date'],
            }
            logger.debug(
                f"[Counting] {zone_name}: direction={direction}, "
                f"in={summary['in_count']}, out={summary['out_count']}, "
                f"current={summary['current_count']}, task_total={task_totals['task_total']}"
            )
            try:
                self.emit(payload)
            except Exception as e:
                logger.error(f"[Counting] 事件发送失败: {e}")
        except Exception as e:
            logger.error(f"[Counting] 计数更新失败: {zone_name} {direction}: {e}")

    def _ensure_day(self):
        """按天复位：日期切换时清空当日内存态"""
        today = self.stats_store.today()
        if today != self._current_date:
            self._current_date = today
            for tracker in self.line_trackers:
                try:
                    tracker.reset_day()
                except Exception:
                    pass
            for s in self._counted_tracks.values():
                s.clear()
            logger.info(f"[Counting] {self.camera_id} 检测到跨天，计数已复位到 {today}")