"""
摄像机状态管理模块

核心职责:
- 管理单个相机的跟踪对象状态（tracked_objects 字典）
- 比较新旧跟踪状态,检测新增/更新/消失的目标
- 触发事件回调（start / update / end / autotrack）供 TrackedObjectProcessor 消费
- 维护 motion_boxes 和 regions 信息（供 AI 视频流推送）

设计说明:
- 保留 PTZ autotrack 回调接口,与项目 src/cameras/ptz_service.py 配合
  - ptz_service.py 提供 ONVIF PTZ 手动控制（PTZController/PTZServiceManager）
  - 本类的 "autotrack" 回调将目标位置变化通知 TrackedObjectProcessor
  - TrackedObjectProcessor 通过 PTZMetrics 共享状态驱动 PTZ 自动跟踪
- 使用 Dict 配置
- 帧回读由 TrackedObjectProcessor 通过共享内存完成
- 事件回调签名适配项目的 EventOutput 接口

事件类型:
- "start": 新目标出现 → 触发事件开始
- "update": 目标状态显著变化 → 触发事件更新
- "end": 目标消失 → 触发事件结束
- "autotrack": 目标位置明显移动 → 触发 PTZ 自动跟踪
"""

import logging
import threading
from collections import defaultdict
from typing import Callable, Optional

import numpy as np

from src.detection.zone_manager import ZoneManager

logger = logging.getLogger(__name__)


class CameraState:
    """
    单个相机的状态管理

    由 TrackedObjectProcessor 在事件处理进程中创建和管理。
    每帧通过 update() 接收 NorfairTracker 输出的跟踪对象字典,
    比较新旧状态并触发事件回调。

    注意: 本类不直接访问视频帧。快照生成由 TrackedObjectProcessor
    在事件回调中通过共享内存回读 YUV 帧完成。
    """

    def __init__(
        self,
        camera_id: str,
        frame_shape: tuple,
        threshold: float = 0.5,
        stationary_threshold: int = 10,
        zones_config: Optional[list] = None,
    ):
        """
        Args:
            camera_id: 摄像机ID
            frame_shape: 帧形状 (height, width)
            threshold: 检测分数阈值（用于 false_positive 判断）
            stationary_threshold: 静止判断阈值
            zones_config: Zone 配置列表
        """
        self.name = camera_id
        self.camera_id = camera_id
        self.frame_shape = frame_shape
        self.threshold = threshold
        self.stationary_threshold = stationary_threshold
        self.zones_config = zones_config or []

        # 跟踪对象状态: {obj_id: dict}
        # dict 是 TrackedObject.to_dict() 的输出
        self.tracked_objects: dict[str, dict] = {}

        # 最佳对象（按 label 分类的最高分对象）: {label: dict}
        self.best_objects: dict[str, dict] = {}

        # 对象计数
        self.object_counts: dict[str, int] = defaultdict(int)
        self.active_object_counts: dict[str, int] = defaultdict(int)

        # Zone 区域检测管理器（zone 检测在 TrackedObject 内部完成，
        # 此处仅保留用于查询启用了徘徊检测的区域，供按需预读帧判断）
        self.zone_manager = ZoneManager(self.zones_config, frame_shape)

        # 当前帧信息
        self.current_frame_time: float = 0.0
        self.motion_boxes: list = []
        self.regions: list = []

        # 事件回调: {"start": [callback], "update": [callback], "end": [callback]}
        self.callbacks: dict[str, list] = defaultdict(list)

        # 线程锁（保护 tracked_objects 和帧信息）
        self._lock = threading.Lock()

        logger.debug(
            f"[CameraState-{camera_id}] 初始化: "
            f"frame_shape={frame_shape}, threshold={threshold}"
        )

    def on(self, event_type: str, callback: Callable):
        """
        注册事件回调

        Args:
            event_type: 事件类型 ("start", "update", "end")
            callback: 回调函数,签名:
                - "start": callback(camera_id, obj_dict, frame_time)
                - "update": callback(camera_id, obj_dict, frame_time)
                - "end": callback(camera_id, obj_dict, frame_time)
        """
        self.callbacks[event_type].append(callback)

    def update(
        self,
        frame_time: float,
        current_tracked_objects: dict[str, dict],
        motion_boxes: Optional[list] = None,
        regions: Optional[list] = None,
    ):
        """
        更新相机状态,触发事件回调

        比较新旧跟踪对象,检测:
        - 新增目标 → 触发 "start" 回调
        - 更新目标 → 触发 "update" 回调（如果有显著变化）
        - 消失目标 → 触发 "end" 回调

        Args:
            frame_time: 当前帧时间戳
            current_tracked_objects: 当前帧的跟踪对象字典
                {obj_id: TrackedObject.to_dict() 输出}
            motion_boxes: 运动框列表
            regions: 检测区域列表
        """
        with self._lock:
            # 更新帧信息
            self.current_frame_time = frame_time
            if motion_boxes is not None:
                self.motion_boxes = motion_boxes
            if regions is not None:
                self.regions = regions

            # 计算新增/消失/更新的对象ID
            current_ids = set(current_tracked_objects.keys())
            previous_ids = set(self.tracked_objects.keys())

            new_ids = current_ids - previous_ids
            removed_ids = previous_ids - current_ids
            updated_ids = current_ids & previous_ids

            # 处理新增目标
            # 注意：current_zones / entered_zones / pending_loitering 由 TrackedObject 维护，
            # 直接随 to_dict() 输出，此处不再覆盖。
            for obj_id in new_ids:
                obj_dict = current_tracked_objects[obj_id]
                self.tracked_objects[obj_id] = obj_dict

                # 更新计数
                label = obj_dict.get("label", "unknown")
                self.object_counts[label] += 1
                if obj_dict.get("active", False):
                    self.active_object_counts[label] += 1

                # 更新最佳对象
                self._update_best_object(obj_dict)

                # 触发 "start" 回调
                for callback in self.callbacks.get("start", []):
                    try:
                        callback(self.camera_id, obj_dict, frame_time)
                    except Exception as e:
                        logger.error(
                            f"[CameraState-{self.camera_id}] start 回调失败: {e}",
                            exc_info=True,
                        )

            # 处理更新目标
            for obj_id in updated_ids:
                old_obj = self.tracked_objects[obj_id]
                new_obj = current_tracked_objects[obj_id]

                # 检测显著变化
                significant_change = self._is_significant_change(old_obj, new_obj)

                # 检测 PTZ 自动跟踪触发（位置明显移动）
                autotracker_update = self._is_autotracker_update(old_obj, new_obj)

                # 更新状态
                self.tracked_objects[obj_id] = new_obj

                # 更新最佳对象
                self._update_best_object(new_obj)

                # 更新活动计数
                old_active = old_obj.get("active", False)
                new_active = new_obj.get("active", False)
                if old_active != new_active:
                    label = new_obj.get("label", "unknown")
                    if new_active:
                        self.active_object_counts[label] += 1
                    else:
                        self.active_object_counts[label] = max(
                            0, self.active_object_counts[label] - 1
                        )

                # autotrack 回调（PTZ 自动跟踪）
                # 显著变化或位置移动均触发,由 TrackedObjectProcessor
                # 通过 PTZMetrics 通知 PTZ 控制进程（基于 ptz_service.py 的 PTZController）
                if autotracker_update or significant_change:
                    for callback in self.callbacks.get("autotrack", []):
                        try:
                            callback(self.camera_id, new_obj, frame_time)
                        except Exception as e:
                            logger.error(
                                f"[CameraState-{self.camera_id}] autotrack 回调失败: {e}",
                                exc_info=True,
                            )

                # 显著变化时触发 "update" 回调
                if significant_change:
                    for callback in self.callbacks.get("update", []):
                        try:
                            callback(self.camera_id, new_obj, frame_time)
                        except Exception as e:
                            logger.error(
                                f"[CameraState-{self.camera_id}] update 回调失败: {e}",
                                exc_info=True,
                            )

            # 处理消失目标
            for obj_id in removed_ids:
                old_obj = self.tracked_objects[obj_id]

                # 设置结束时间
                old_obj["end_time"] = frame_time

                # 更新计数
                label = old_obj.get("label", "unknown")
                self.object_counts[label] = max(0, self.object_counts[label] - 1)
                if old_obj.get("active", False):
                    self.active_object_counts[label] = max(
                        0, self.active_object_counts[label] - 1
                    )

                # 触发 "end" 回调
                for callback in self.callbacks.get("end", []):
                    try:
                        callback(self.camera_id, old_obj, frame_time)
                    except Exception as e:
                        logger.error(
                            f"[CameraState-{self.camera_id}] end 回调失败: {e}",
                            exc_info=True,
                        )

                # 从跟踪对象中移除
                del self.tracked_objects[obj_id]

    def _is_significant_change(self, old_obj: dict, new_obj: dict) -> bool:
        """
        判断目标是否有显著变化

        显著变化的条件:
        - active 状态切换（活动↔静止）
        - current_zones 变化
        - position_changes 变化
        - 分数显著提升（top_score 变化）
        - 距离上次更新超过 60 秒
        """
        # active 状态切换
        if old_obj.get("active") != new_obj.get("active"):
            return True

        # current_zones 变化
        if set(old_obj.get("current_zones", [])) != set(
            new_obj.get("current_zones", [])
        ):
            return True

        # position_changes 变化
        if old_obj.get("position_changes", 0) != new_obj.get("position_changes", 0):
            return True

        # 分数显著提升
        old_score = old_obj.get("top_score", 0)
        new_score = new_obj.get("top_score", 0)
        if new_score > old_score + 0.1:
            return True

        # 距离上次更新超过 60 秒
        old_time = old_obj.get("frame_time", 0)
        new_time = new_obj.get("frame_time", 0)
        if new_time - old_time > 60:
            return True

        return False

    def _is_autotracker_update(self, old_obj: dict, new_obj: dict) -> bool:
        """
        判断目标是否需要触发 PTZ 自动跟踪更新

        目标位置发生明显移动时触发,
        供 PTZ 自动跟踪逻辑（基于 src/cameras/ptz_service.py 的 PTZController）
        更新 PTZ 控制指令。

        触发条件:
        - box 中心点位移超过阈值（默认 10 像素）,表明目标在画面中移动
        - position_changes 增加,表明目标发生显著位移事件

        Args:
            old_obj: 上一帧的目标字典
            new_obj: 当前帧的目标字典

        Returns:
            bool: 是否需要触发 autotrack 回调
        """
        AUTOTRACK_PIXEL_THRESHOLD = 10  # 中心点位移阈值（像素）

        old_box = old_obj.get("box", [0, 0, 0, 0])
        new_box = new_obj.get("box", [0, 0, 0, 0])

        # 比较 box 中心点位移
        if len(old_box) >= 4 and len(new_box) >= 4:
            old_cx = (old_box[0] + old_box[2]) / 2.0
            old_cy = (old_box[1] + old_box[3]) / 2.0
            new_cx = (new_box[0] + new_box[2]) / 2.0
            new_cy = (new_box[1] + new_box[3]) / 2.0
            displacement = ((new_cx - old_cx) ** 2 + (new_cy - old_cy) ** 2) ** 0.5
            if displacement > AUTOTRACK_PIXEL_THRESHOLD:
                return True

        # position_changes 增加也触发
        if new_obj.get("position_changes", 0) > old_obj.get("position_changes", 0):
            return True

        return False

    def _update_best_object(self, obj_dict: dict):
        """更新最佳对象（按 label 分类,保留最高分）"""
        label = obj_dict.get("label", "unknown")
        score = obj_dict.get("top_score", obj_dict.get("score", 0))

        if label not in self.best_objects or score > self.best_objects[label].get(
            "top_score", 0
        ):
            self.best_objects[label] = obj_dict.copy()

    def finished(self, obj_id: str):
        """标记对象已结束（从跟踪对象中移除）"""
        with self._lock:
            if obj_id in self.tracked_objects:
                del self.tracked_objects[obj_id]

    def get_tracked_objects(self) -> dict:
        """获取当前跟踪对象字典（线程安全）"""
        with self._lock:
            return {k: v.copy() for k, v in self.tracked_objects.items()}

    def get_object_counts(self) -> dict:
        """获取对象计数"""
        with self._lock:
            return {
                "total": dict(self.object_counts),
                "active": dict(self.active_object_counts),
            }

    def get_current_frame_info(self) -> dict:
        """获取当前帧信息（用于 AI 视频流推送）"""
        with self._lock:
            return {
                "frame_time": self.current_frame_time,
                "motion_boxes": self.motion_boxes.copy(),
                "regions": self.regions.copy(),
                "tracked_objects": {
                    k: v.copy() for k, v in self.tracked_objects.items()
                },
            }
