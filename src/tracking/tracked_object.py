"""
跟踪对象状态机

核心职责：
- 管理单个跟踪目标的状态（score_history、motionless_count、position_changes）
- 判断 false_positive（基于 computed_score 与 threshold）
- 维护最佳缩略图信息（thumbnail_data，由 TrackedObjectProcessor 回读共享内存生成实际图片）
- 检测显著变化（zone 变化、位置变化、状态切换）以触发事件更新

设计说明：
- 不含 PTZ autotrack 逻辑（由 CameraState 回调 + PTZMetrics 处理）
- 不含 Attributes/Logos 属性识别
- 不含 Review/Severity 审查系统
- 保留基础 zone 进出检测，不含 Zone loitering 复杂逻辑
- 不直接依赖 frame_cache（只存储 thumbnail_data，帧回读由外部完成）
- 配置从 Dict 读取

状态机说明（由 TrackedObjectProcessor 根据 active/end_time 推断）：
  entering → active → stationary → leaving → ended
"""

import logging
from collections import defaultdict
from statistics import median
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ============================================================
# 辅助函数
# ============================================================

def area(box) -> float:
    """计算边界框面积"""
    return int((box[2] - box[0]) * (box[3] - box[1]))


def on_edge(box, frame_shape) -> bool:
    """判断边界框是否贴边"""
    return bool(
        box[0] == 0
        or box[1] == 0
        or box[2] == frame_shape[1] - 1
        or box[3] == frame_shape[0] - 1
    )


def is_better_thumbnail(current_thumb: dict, new_obj: dict, frame_shape) -> bool:
    """
    判断新检测框是否更适合作为缩略图

    规则：
    - 贴边的框优先级低
    - 分数高 5% 以上优先
    - 面积大 10% 以上优先
    """
    # 如果新框贴边而当前框不贴边，不更新
    if on_edge(new_obj["box"], frame_shape) and not on_edge(
        current_thumb["box"], frame_shape
    ):
        return False

    # 分数高 5% 以上，更新
    if new_obj["score"] > current_thumb["score"] + 0.05:
        return True

    # 面积大 10% 以上，更新
    if new_obj["area"] > current_thumb["area"] * 1.1:
        return True

    return False


# ============================================================
# TrackedObject 状态机
# ============================================================

class TrackedObject:
    """
    单个跟踪目标的状态机

    由 NorfairTracker 在 register() 时创建，每帧通过 update() 更新状态。
    to_dict() 输出事件数据，由 TrackedObjectProcessor 消费生成事件。

    注意：本类不直接访问视频帧。thumbnail_data 只记录最佳帧的元信息
    （frame_time、box、area、region、score），实际图片由 TrackedObjectProcessor
    通过共享内存 shm_block_id 回读 YUV 转 RGB 生成。
    """

    def __init__(
        self,
        camera_id: str,
        frame_shape: tuple,
        obj_data: dict,
        threshold: float = 0.5,
        stationary_threshold: int = 10,
        zones_config: Optional[list] = None,
        fps: int = 25,
    ):
        """
        Args:
            camera_id: 摄像机ID
            frame_shape: 帧形状 (height, width)
            obj_data: 初始对象数据，必须包含:
                - score_history: list[float] 初始分数历史
                - label: 类别名称
                - score: 当前检测分数
                - box: [x1, y1, x2, y2]
                - area: 框面积
                - ratio: 宽高比
                - region: 检测区域 [x1, y1, x2, y2]
                - frame_time: 帧时间戳
                - centroid: (cx, cy) 中心点
                - id: 跟踪ID
                - start_time: 开始时间
                - motionless_count: 静止计数（初始0）
                - position_changes: 位置变化次数（初始0）
            threshold: false_positive 判断分数阈值
            stationary_threshold: 静止判断阈值（motionless_count 超过此值认为静止）
            zones_config: Zone 配置列表，每个 zone 是 dict:
                {"name": str, "contour": np.ndarray, "objects": list[str],
                 "inertia": int, "enable_loitering_detection": bool,
                 "loitering_time": int}
                contour 应为预计算的像素轮廓（np.ndarray），多个 TrackedObject 共享同一引用。
            fps: 检测帧率，用于将 loitering_time（秒）换算为帧数阈值
        """
        # 分离 score_history（不属于持久状态）
        self.score_history = obj_data["score_history"]
        del obj_data["score_history"]

        self.camera_id = camera_id
        self.frame_shape = frame_shape
        self.obj_data = obj_data
        self.threshold = threshold
        self.stationary_threshold = stationary_threshold
        self.zones_config = zones_config or []
        self.fps = fps

        # Zone 状态
        self.zone_presence: dict[str, int] = {}  # 进入惯性计数器（连续 N 帧确认进入）
        self.zone_loitering: dict[str, int] = {}  # 徘徊帧计数器（累计停留帧数）
        self.current_zones: list[str] = []  # 当前确认所在的区域
        self.entered_zones: list[str] = []  # 历史上曾进入的区域（只增不减）
        self.pending_loitering: list[str] = []  # 当前帧已达到徘徊阈值的区域（瞬时，每帧重建）
        self.loitered_zones: list[str] = []  # 生命周期内累计曾徘徊的区域（只增不减，用于事件落库）

        # 更新计数器（用于低频调试日志，每 60 帧 print 一次 zone 检测状态）
        self._update_count = 0

        # 状态标记
        self.false_positive = True
        self.top_score = 0.0
        self.computed_score = 0.0
        self.thumbnail_data: Optional[dict] = None
        self.last_updated = 0.0
        self.last_published = 0.0
        self.active = True

        # 初始快照（用于 significant_change 比较）
        self.previous = self.to_dict()

    def compute_score(self) -> float:
        """取分数历史的中位数作为计算分数"""
        return median(self.score_history) if self.score_history else 0.0

    def _is_false_positive(self) -> bool:
        """一旦确认为真正目标，不再变为 false_positive"""
        if not self.false_positive:
            return False
        return self.computed_score < self.threshold

    def is_active(self) -> bool:
        """是否处于活动状态（非静止）"""
        return not self.is_stationary()

    def is_stationary(self) -> bool:
        """是否处于静止状态（motionless_count 超过阈值）"""
        return self.obj_data["motionless_count"] > self.stationary_threshold

    def update(
        self,
        current_frame_time: float,
        obj_data: dict,
        has_valid_frame: bool,
    ) -> tuple:
        """
        更新跟踪对象状态

        Args:
            current_frame_time: 当前帧时间戳
            obj_data: 当前帧的检测数据
            has_valid_frame: 是否有有效帧可用于缩略图

        Returns:
            (thumb_update, significant_change): 缩略图是否更新、是否有显著变化
        """
        thumb_update = False
        significant_change = False
        self._update_count += 1

        # 如果目标不在当前帧，添加 0.0 分数；否则添加实际分数
        if obj_data["frame_time"] != current_frame_time:
            self.score_history.append(0.0)
        else:
            self.score_history.append(obj_data["score"])

        # 只保留最近 10 个分数
        if len(self.score_history) > 10:
            self.score_history = self.score_history[-10:]

        # 计算 computed_score 和 false_positive
        self.computed_score = self.compute_score()
        if self.computed_score > self.top_score:
            self.top_score = self.computed_score
        self.false_positive = self._is_false_positive()
        self.active = self.is_active()

        # 更新最佳缩略图（仅非 false_positive 且有有效帧时）
        if not self.false_positive and has_valid_frame:
            if self.thumbnail_data is None or is_better_thumbnail(
                self.thumbnail_data,
                obj_data,
                self.frame_shape,
            ):
                self.thumbnail_data = {
                    "frame_time": current_frame_time,
                    "box": obj_data["box"],
                    "area": obj_data["area"],
                    "region": obj_data["region"],
                    "score": obj_data["score"],
                }
                thumb_update = True

        # Zone 检测：inertia 去抖 + loitering 帧计数
        current_zones = []
        bottom_center = (obj_data["centroid"][0], obj_data["box"][3])
        new_pending_loitering = []

        for zone in self.zones_config:
            name = zone.get("name", "")
            contour = zone.get("contour")
            zone_objects = zone.get("objects", [])
            inertia = zone.get("inertia", 3)
            enable_loitering = zone.get("enable_loitering_detection", False)
            loitering_time = zone.get("loitering_time", 0)

            # 白名单过滤：zone 限定对象类型且不含当前目标，跳过该 zone
            if zone_objects and obj_data["label"] not in zone_objects:
                continue

            if contour is None:
                continue

            zone_score = self.zone_presence.get(name, 0) + 1
            in_zone = cv2.pointPolygonTest(contour, bottom_center, False) >= 0

            # 调试日志：启用徘徊检测时，每 60 帧打印一次 zone 检测状态，便于排查徘徊不触发问题
            if enable_loitering and self._update_count % 60 == 0:
                threshold = loitering_time * self.fps
                cx, cy = bottom_center
                logger.debug(
                    f"[TrackedObject-{self.obj_data['id']}] zone={name} "
                    f"in_zone={in_zone} pos=({cx:.0f},{cy:.0f}) "
                    f"contour=[{contour[:,0].min():.0f},{contour[:,1].min():.0f}]-[{contour[:,0].max():.0f},{contour[:,1].max():.0f}] "
                    f"zone_score={zone_score}/{inertia} "
                    f"loitering={self.zone_loitering.get(name, 0)}/{threshold} "
                    f"(time={loitering_time}s*fps={self.fps})"
                )

            if in_zone:
                # 惯性去抖：连续 inertia 帧才算"确认在 zone 内"
                if zone_score >= inertia:
                    current_zones.append(name)
                    if name not in self.entered_zones:
                        self.entered_zones.append(name)
                    # 徘徊帧计数：仅当启用且阈值>0时累计
                    if enable_loitering and loitering_time > 0:
                        loitering_score = self.zone_loitering.get(name, 0) + 1
                        self.zone_loitering[name] = loitering_score
                        threshold = loitering_time * self.fps
                        # 达到 loitering_time * fps 帧阈值，标记为已徘徊
                        if loitering_score >= threshold:
                            if name not in new_pending_loitering:
                                new_pending_loitering.append(name)
                            # 累计历史徘徊区域（只增不减，用于事件落库与前端展示）
                            if name not in self.loitered_zones:
                                self.loitered_zones.append(name)
                                logger.info(
                                    f"[TrackedObject-{self.obj_data['id']}] ✅ 徘徊触发 "
                                    f"zone={name} score={loitering_score}/{threshold}"
                                )
                    else:
                        self.zone_loitering[name] = 0
                else:
                    self.zone_presence[name] = zone_score
            else:
                # 离开 zone：惯性未达阈值则递减
                if 0 < zone_score < inertia:
                    self.zone_presence[name] = zone_score - 1
                # 离开 zone 重置徘徊计数
                self.zone_loitering[name] = 0

        self.pending_loitering = new_pending_loitering

        # 检测显著变化
        if not self.false_positive:
            # zone 变化
            if set(self.current_zones) != set(current_zones):
                significant_change = True

            # 位置变化次数改变
            if self.obj_data["position_changes"] != obj_data["position_changes"]:
                significant_change = True

            # 状态切换（active ↔ stationary）
            if self.previous["active"] != self.active:
                significant_change = True

            # 至少每 60 秒更新一次
            if self.obj_data["frame_time"] - self.previous["frame_time"] > 60:
                significant_change = True

        # 提交更新
        self.obj_data.update(obj_data)
        self.current_zones = current_zones
        return (thumb_update, significant_change)

    def to_dict(self, include_thumbnail: bool = False) -> dict:
        """
        转换为事件字典

        Args:
            include_thumbnail: 是否包含 base64 缩略图（需要帧访问，通常由
                              TrackedObjectProcessor 在需要时生成）

        Returns:
            事件数据字典
        """
        event = {
            "id": self.obj_data["id"],
            "camera": self.camera_id,
            "frame_time": self.obj_data["frame_time"],
            "snapshot": self.thumbnail_data,
            "label": self.obj_data["label"],
            "top_score": self.top_score,
            "false_positive": self.false_positive,
            "start_time": self.obj_data["start_time"],
            "end_time": self.obj_data.get("end_time", None),
            "score": self.obj_data["score"],
            "box": self.obj_data["box"],
            "area": self.obj_data["area"],
            "ratio": self.obj_data["ratio"],
            "region": self.obj_data["region"],
            "estimate": self.obj_data.get("estimate"),
            "active": self.active,
            "stationary": not self.active,
            "motionless_count": self.obj_data["motionless_count"],
            "position_changes": self.obj_data["position_changes"],
            "current_zones": self.current_zones.copy(),
            "entered_zones": self.entered_zones.copy(),
            "pending_loitering": self.pending_loitering.copy(),
            "loitered_zones": self.loitered_zones.copy(),
        }

        return event
