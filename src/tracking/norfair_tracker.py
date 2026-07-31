"""
Norfair 跟踪器

核心职责:
- 使用 Norfair 库进行目标跟踪（基于 Kalman 滤波 + 自定义距离函数）
- 管理跟踪对象的生命周期（register / update / deregister）
- 通过 IoU 历史判断目标静止/活动状态（motionless_count / position_changes）
- 提供 match_and_update() 接口,每帧将检测结果喂给跟踪器并更新状态
- 保留 PTZ autotrack 接口（coord_transformations）,与项目 ptz_service.py 配合

配置说明:
- 配置从 Dict 读取（detect_config）
- PTZMetrics 使用 src/tracking/ptz_metrics.py
- PtzMotionEstimator 作为可选组件（占位,后续实现完整 autotrack）
- TrackedObject 使用 src/tracking/tracked_object.py
"""

import logging
import random
import string
from typing import Optional

import numpy as np
from norfair import (
    Detection,
    OptimizedKalmanFilterFactory,
    Tracker,
)

from src.tracking.ptz_metrics import PTZMetrics
from src.tracking.tracked_object import TrackedObject, area

logger = logging.getLogger(__name__)


# ============================================================
# 距离函数
# ============================================================

# 静止/活动判断的 IoU 阈值
THRESHOLD_KNOWN_ACTIVE_IOU = 0.2
THRESHOLD_STATIONARY_CHECK_IOU = 0.6
THRESHOLD_ACTIVE_CHECK_IOU = 0.9
MAX_STATIONARY_HISTORY = 10


def distance(detection: np.ndarray, estimate: np.ndarray) -> float:
    """
    计算检测框与估计框之间的归一化距离

    距离计算方式:
    - 考虑底中心点位置的相对位移（相对框尺寸归一化）
    - 考虑宽高比的变化
    - 返回变化向量的欧几里得距离

    Args:
        detection: 检测框 [[x1,y1],[x2,y2]]
        estimate: 估计框 [[x1,y1],[x2,y2]]

    Returns:
        float: 距离值（越小越相似）
    """
    estimate_dim = np.diff(estimate, axis=0).flatten()
    detection_dim = np.diff(detection, axis=0).flatten()

    # 底中心位置
    detection_position = np.array(
        [np.average(detection[:, 0]), np.max(detection[:, 1])]
    )
    estimate_position = np.array([np.average(estimate[:, 0]), np.max(estimate[:, 1])])

    # 相对位移（按框尺寸归一化）
    dist = (detection_position - estimate_position).astype(float)
    dist[0] /= estimate_dim[0] if estimate_dim[0] != 0 else 1
    dist[1] /= estimate_dim[1] if estimate_dim[1] != 0 else 1

    # 宽高比变化
    widths = np.sort([estimate_dim[0], detection_dim[0]])
    heights = np.sort([estimate_dim[1], detection_dim[1]])
    width_ratio = widths[1] / widths[0] - 1.0 if widths[0] != 0 else 0
    height_ratio = heights[1] / heights[0] - 1.0 if heights[0] != 0 else 0

    # 变化向量 = [相对x位移, 相对y位移, 宽比, 高比]
    change = np.append(dist, np.array([width_ratio, height_ratio]))

    return np.linalg.norm(change)


def tracking_distance(detection: Detection, tracked_object) -> float:
    """norfair Tracker 使用的距离函数包装"""
    return distance(detection.points, tracked_object.estimate)


# ============================================================
# 辅助函数
# ============================================================

def intersection_over_union(box_a, box_b) -> float:
    """计算两个框的 IoU"""
    # 确定交集区域
    xA = max(box_a[0], box_b[0])
    yA = max(box_a[1], box_b[1])
    xB = min(box_a[2], box_b[2])
    yB = min(box_a[3], box_b[3])

    inter_area = max(0, xB - xA) * max(0, yB - yA)
    box_a_area = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    box_b_area = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])

    union_area = box_a_area + box_b_area - inter_area
    if union_area == 0:
        return 0.0

    return inter_area / union_area


def average_boxes(boxes: list) -> list:
    """计算多个框的平均框"""
    if not boxes:
        return [0, 0, 0, 0]
    arr = np.array(boxes)
    return arr.mean(axis=0).tolist()


def median_of_boxes(boxes: list) -> list:
    """计算多个框的中位数框"""
    if not boxes:
        return [0, 0, 0, 0]
    arr = np.array(boxes)
    return arr.median(axis=0) if hasattr(arr, "median") else np.median(arr, axis=0).tolist()


# ============================================================
# NorfairTracker 跟踪器
# ============================================================

class NorfairTracker:
    """
    Norfair 目标跟踪器

    每帧通过 match_and_update() 喂入检测结果,跟踪器:
    1. 将检测框转换为 norfair Detection
    2. 调用 norfair Tracker.update() 进行匹配（基于 Kalman 滤波预测 + 距离函数）
    3. 对新目标调用 register(),对已有目标调用 update()
    4. 清理过期目标
    5. 维护 untracked_object_boxes（未匹配到跟踪对象的检测框,用于 region 生成）

    静止/活动状态判断:
    - 维护 stationary_box_history（最近 MAX_STATIONARY_HISTORY 个框）
    - 计算当前框与历史平均框的 IoU
    - IoU 高 → 目标静止 → motionless_count++
    - IoU 低 → 目标移动 → motionless_count=0, position_changes++
    - motionless_count 超过 stationary_threshold → 判定为静止状态

    PTZ autotrack 接口:
    - 当 ptz_metrics.autotracker_enabled 为 True 时,使用 coord_transformations 补偿相机运动
    - PtzMotionEstimator 后续实现（与项目 ptz_service.py 对接）
    """

    def __init__(
        self,
        camera_id: str,
        frame_shape: tuple,
        detect_config: dict,
        ptz_metrics: Optional[PTZMetrics] = None,
        zones_config: Optional[list] = None,
    ):
        """
        Args:
            camera_id: 摄像机ID
            frame_shape: 帧形状 (height, width)
            detect_config: 检测配置 dict,支持以下字段:
                - min_initialized: 最小初始化帧数（默认 3）
                - max_disappeared: 最大消失帧数（默认 30）
                - stationary_threshold: 静止判断阈值（motionless_count 超过此值认为静止,默认 10）
                - threshold: false_positive 分数阈值（默认 0.5）
                - max_frames: 静止对象最大帧数 dict {label: frames} 或 {"default": frames}
            ptz_metrics: PTZ 共享状态（可选,启用 autotrack 时传入）
            zones_config: Zone 配置列表（可选）
        """
        self.camera_id = camera_id
        self.frame_shape = frame_shape
        self.detect_config = detect_config
        self.ptz_metrics = ptz_metrics
        self.zones_config = zones_config

        # 检测配置参数
        self.min_initialized = detect_config.get("min_initialized", 3)
        self.max_disappeared = detect_config.get("max_disappeared", 30)
        self.stationary_threshold = detect_config.get("stationary_threshold", 10)
        self.threshold = detect_config.get("threshold", 0.5)
        self.max_frames_config = detect_config.get("max_frames", {})

        # 帧尺寸（用于裁剪估计框边界）
        self.frame_width = frame_shape[1]
        self.frame_height = frame_shape[0]

        # 跟踪状态
        self.tracked_objects: dict[str, TrackedObject] = {}
        self.untracked_object_boxes: list[list[int]] = []
        self.disappeared: dict[str, int] = {}
        self.positions: dict[str, dict] = {}
        self.stationary_box_history: dict[str, list] = {}
        self.track_id_map: dict = {}  # norfair global_id -> 跟踪对象 id

        # 轨迹历史：obj_id -> [(cx, cy), ...] 逐帧质心点序列
        # 存储端保留足够长度，显示端按 display_config.trajectory_history_length 截取
        self.trajectory_history: dict[str, list] = {}
        self.max_trajectory_length = 100

        # PTZ 运动估计器（后续实现完整 autotrack 时补充）
        self.ptz_motion_estimator = None

        # 初始化 norfair Tracker
        # 使用 "iou" 向量化距离函数（Norfair 内置，自动按label分组匹配）
        # IoU 距离阈值：1 - IoU，越小表示要求匹配越严格
        # distance_threshold=0.7 等价于 IoU > 0.3 即可匹配
        self.tracker = Tracker(
            distance_function="iou",
            distance_threshold=0.7,
            initialization_delay=self.min_initialized,
            hit_counter_max=self.max_disappeared,
            filter_factory=OptimizedKalmanFilterFactory(R=3.4),
        )

        logger.info(
            f"[NorfairTracker-{camera_id}] 初始化完成: "
            f"min_initialized={self.min_initialized}, "
            f"max_disappeared={self.max_disappeared}, "
            f"stationary_threshold={self.stationary_threshold}, "
            f"ptz_autotrack={ptz_metrics.is_autotracker_enabled() if ptz_metrics else False}"
        )

    def register(self, track_id, obj: dict):
        """注册新跟踪对象"""
        # 生成唯一 ID
        rand_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        obj_id = f"{obj['frame_time']}-{rand_id}"

        self.track_id_map[track_id] = obj_id
        obj["id"] = obj_id
        obj["start_time"] = obj["frame_time"]
        obj["motionless_count"] = 0
        obj["position_changes"] = 0

        # 从 norfair 跟踪对象的历史检测中提取分数历史
        norfair_obj = next(
            (o for o in self.tracker.tracked_objects if o.global_id == track_id), None
        )
        if norfair_obj and norfair_obj.past_detections:
            obj["score_history"] = [
                p.data["score"] for p in norfair_obj.past_detections
            ]
        else:
            obj["score_history"] = [obj.get("score", 0.0)]

        # 创建 TrackedObject
        self.tracked_objects[obj_id] = TrackedObject(
            camera_id=self.camera_id,
            frame_shape=self.frame_shape,
            obj_data=obj,
            threshold=self.threshold,
            stationary_threshold=self.stationary_threshold,
            zones_config=self.zones_config,
        )
        self.disappeared[obj_id] = 0
        self.positions[obj_id] = {
            "xmins": [],
            "ymins": [],
            "xmaxs": [],
            "ymaxs": [],
            "xmin": 0,
            "ymin": 0,
            "xmax": self.frame_width,
            "ymax": self.frame_height,
        }
        self.stationary_box_history[obj_id] = []

        # 初始化轨迹历史（记录质心点）
        box = obj.get("box", [0, 0, 0, 0])
        cx = int((box[0] + box[2]) / 2)
        cy = int((box[1] + box[3]) / 2)
        self.trajectory_history[obj_id] = [(cx, cy)]

    def deregister(self, obj_id: str, track_id):
        """注销跟踪对象"""
        if obj_id in self.tracked_objects:
            del self.tracked_objects[obj_id]
        if obj_id in self.disappeared:
            del self.disappeared[obj_id]
        if obj_id in self.positions:
            del self.positions[obj_id]
        if obj_id in self.stationary_box_history:
            del self.stationary_box_history[obj_id]
        if obj_id in self.trajectory_history:
            del self.trajectory_history[obj_id]

        # 从 norfair tracker 中移除
        self.tracker.tracked_objects = [
            o for o in self.tracker.tracked_objects if o.global_id != track_id
        ]
        if track_id in self.track_id_map:
            del self.track_id_map[track_id]

    def update_position(self, obj_id: str, box: list, stationary: bool) -> bool:
        """
        更新目标位置,判断是否静止

        维护最近 MAX_STATIONARY_HISTORY 个框,计算当前框与历史平均框的 IoU:
        - IoU < THRESHOLD_KNOWN_ACTIVE_IOU(0.2): 目标明显移动,重置位置历史,返回 False
        - IoU < threshold(静止0.6/活动0.9): 用中位数框二次验证,仍低则判定移动
        - IoU 高: 目标静止,累积位置历史,返回 True

        Returns:
            bool: True 表示目标静止（motionless_count++）,False 表示目标移动
        """
        xmin, ymin, xmax, ymax = box
        position = self.positions[obj_id]
        self.stationary_box_history[obj_id].append(box)

        # 限制历史长度
        if len(self.stationary_box_history[obj_id]) > MAX_STATIONARY_HISTORY:
            self.stationary_box_history[obj_id] = self.stationary_box_history[obj_id][
                -MAX_STATIONARY_HISTORY:
            ]

        # 计算与历史平均框的 IoU
        avg_iou = intersection_over_union(
            box, average_boxes(self.stationary_box_history[obj_id])
        )

        # IoU 很低,目标明显在移动
        if avg_iou < THRESHOLD_KNOWN_ACTIVE_IOU:
            self.positions[obj_id] = {
                "xmins": [xmin],
                "ymins": [ymin],
                "xmaxs": [xmax],
                "ymaxs": [ymax],
                "xmin": xmin,
                "ymin": ymin,
                "xmax": xmax,
                "ymax": ymax,
            }
            return False

        # 根据当前状态选择阈值
        threshold = (
            THRESHOLD_STATIONARY_CHECK_IOU if stationary else THRESHOLD_ACTIVE_CHECK_IOU
        )

        # IoU 低于阈值,用中位数框二次验证
        if avg_iou < threshold:
            median_iou = intersection_over_union(
                (
                    position["xmin"],
                    position["ymin"],
                    position["xmax"],
                    position["ymax"],
                ),
                median_of_boxes(self.stationary_box_history[obj_id]),
            )

            # 中位数 IoU 仍低,判定目标不再静止
            if median_iou < threshold:
                self.positions[obj_id] = {
                    "xmins": [xmin],
                    "ymins": [ymin],
                    "xmaxs": [xmax],
                    "ymaxs": [ymax],
                    "xmin": xmin,
                    "ymin": ymin,
                    "xmax": xmax,
                    "ymax": ymax,
                }
                return False

        # 位置历史不足 10 个,累积并重算位置框（用百分位数去噪）
        if len(position["xmins"]) < 10:
            position["xmins"].append(xmin)
            position["ymins"].append(ymin)
            position["xmaxs"].append(xmax)
            position["ymaxs"].append(ymax)
            position["xmin"] = np.percentile(position["xmins"], 15)
            position["ymin"] = np.percentile(position["ymins"], 15)
            position["xmax"] = np.percentile(position["xmaxs"], 85)
            position["ymax"] = np.percentile(position["ymaxs"], 85)

        return True

    def is_expired(self, obj_id: str) -> bool:
        """判断静止对象是否超过 max_frames 限制,需要注销"""
        obj = self.tracked_objects[obj_id]
        label = obj.obj_data["label"]

        # 获取该类别的 max_frames,或使用默认值
        if isinstance(self.max_frames_config, dict):
            max_frames = self.max_frames_config.get(
                label, self.max_frames_config.get("default")
            )
        else:
            max_frames = None

        # 没有配置 max_frames,不限制
        if max_frames is None:
            return False

        # 超过 max_frames,需要注销
        if obj.obj_data["motionless_count"] - self.stationary_threshold > max_frames:
            return True

        return False

    def update(self, track_id, obj: dict):
        """更新已有跟踪对象的状态"""
        obj_id = self.track_id_map[track_id]
        if obj_id not in self.tracked_objects:
            return

        self.disappeared[obj_id] = 0

        tracked_obj = self.tracked_objects[obj_id]
        stationary = tracked_obj.obj_data["motionless_count"] >= self.stationary_threshold

        # 更新位置,判断静止/移动
        if self.update_position(obj_id, obj["box"], stationary):
            # 目标静止,motionless_count++
            tracked_obj.obj_data["motionless_count"] += 1
            if self.is_expired(obj_id):
                self.deregister(obj_id, track_id)
                return
        else:
            # 目标移动
            # 首次位置变化,或从静止恢复移动时,position_changes++
            if (
                tracked_obj.obj_data["position_changes"] == 0
                or tracked_obj.obj_data["motionless_count"] >= self.stationary_threshold
            ):
                tracked_obj.obj_data["position_changes"] += 1
            tracked_obj.obj_data["motionless_count"] = 0
            self.stationary_box_history[obj_id] = []

        # 更新 obj_data
        tracked_obj.obj_data.update(obj)

        # 追加轨迹点（记录质心）
        box = obj.get("box", [0, 0, 0, 0])
        cx = int((box[0] + box[2]) / 2)
        cy = int((box[1] + box[3]) / 2)
        if obj_id not in self.trajectory_history:
            self.trajectory_history[obj_id] = []
        self.trajectory_history[obj_id].append((cx, cy))
        # 限制轨迹长度
        if len(self.trajectory_history[obj_id]) > self.max_trajectory_length:
            self.trajectory_history[obj_id] = self.trajectory_history[obj_id][-self.max_trajectory_length:]

    def update_frame_times(self, frame_name: str, frame_time: float):
        """
        当没有新检测时,用上一帧的检测更新跟踪（保持跟踪连续性）

        用于运动检测无结果但仍需维护跟踪状态的场景。
        """
        detections = [
            (
                obj.obj_data["label"],
                obj.obj_data["score"],
                obj.obj_data["box"],
                obj.obj_data["area"],
                obj.obj_data["ratio"],
                obj.obj_data["region"],
            )
            for id, obj in self.tracked_objects.items()
            if self.disappeared.get(id, 0) == 0
        ]
        self.match_and_update(frame_name, frame_time, detections=detections)

    def match_and_update(
        self,
        frame_name: str,
        frame_time: float,
        detections: list,
    ):
        """
        核心方法:将检测结果喂给 norfair Tracker,更新跟踪状态

        Args:
            frame_name: 帧名称（通常为帧时间戳字符串）
            frame_time: 帧时间戳（float）
            detections: 检测结果列表,每个元素是 tuple/list:
                (label, score, box, area, ratio, region)
                - label: 类别名称
                - score: 检测分数
                - box: [x1, y1, x2, y2]
                - area: 框面积
                - ratio: 宽高比
                - region: 检测区域 [x1, y1, x2, y2]
        """
        # 转换为 norfair Detection
        norfair_detections = []
        for obj in detections:
            label, score, box, obj_area, ratio, region = obj[0], obj[1], obj[2], obj[3], obj[4], obj[5]

            # 中心点（用于下游处理）
            centroid_x = int((box[0] + box[2]) / 2.0)
            centroid_y = int((box[1] + box[3]) / 2.0)

            # 使用左上+右下角点跟踪（而非中心点）
            points = np.array([[box[0], box[1]], [box[2], box[3]]])

            norfair_detections.append(
                Detection(
                    points=points,
                    label=label,
                    data={
                        "label": label,
                        "score": score,
                        "box": box,
                        "area": obj_area,
                        "ratio": ratio,
                        "region": region,
                        "frame_time": frame_time,
                        "centroid": (centroid_x, centroid_y),
                    },
                )
            )

        # PTZ autotrack: 计算坐标变换（补偿相机运动）
        coord_transformations = None
        if self.ptz_metrics and self.ptz_metrics.is_autotracker_enabled():
            # TODO: 后续实现 PtzMotionEstimator,与项目 ptz_service.py 对接
            # 目前 coord_transformations=None,不补偿相机运动
            # 实现后:
            #   if not self.ptz_motion_estimator:
            #       self.ptz_motion_estimator = PtzMotionEstimator(...)
            #   coord_transformations = self.ptz_motion_estimator.motion_estimator(
            #       detections, frame_name, frame_time, self.camera_id
            #   )
            pass

        # 调用 norfair Tracker 更新
        tracked_objects = self.tracker.update(
            detections=norfair_detections,
            coord_transformations=coord_transformations,
        )

        # 更新或创建跟踪对象
        active_ids = []
        for t in tracked_objects:
            estimate = tuple(t.estimate.flatten().astype(int))
            # 限制估计框在图像范围内
            estimate = (
                max(0, estimate[0]),
                max(0, estimate[1]),
                min(self.frame_width - 1, estimate[2]),
                min(self.frame_height - 1, estimate[3]),
            )

            obj = {
                **t.last_detection.data,
                "estimate": estimate,
                "estimate_velocity": getattr(t, "estimate_velocity", None),
            }
            active_ids.append(t.global_id)

            if t.global_id not in self.track_id_map:
                # 新目标,注册
                self.register(t.global_id, obj)
            elif t.last_detection.data["frame_time"] != frame_time:
                # 已有目标,但本帧未检测到,disappeared++
                obj_id = self.track_id_map[t.global_id]
                self.disappeared[obj_id] += 1
                # 仅当估计框有效时更新
                if estimate[0] < estimate[2] and estimate[1] < estimate[3]:
                    self.update(t.global_id, obj)
            else:
                # 已有目标,本帧检测到,正常更新
                self.update(t.global_id, obj)

        # 清理过期跟踪对象
        expired_ids = [k for k in self.track_id_map.keys() if k not in active_ids]
        for e_id in expired_ids:
            obj_id = self.track_id_map[e_id]
            self.deregister(obj_id, e_id)

        # 更新未匹配到跟踪对象的检测框列表（用于 region 生成）
        tracked_object_boxes = [obj.obj_data["box"] for obj in self.tracked_objects.values()]
        self.untracked_object_boxes = [
            o[2] for o in detections if o[2] not in tracked_object_boxes
        ]

    def get_tracked_objects_dict(self) -> dict:
        """获取所有跟踪对象的字典表示（用于跨进程传递，包含轨迹历史）"""
        result = {}
        for obj_id, obj in self.tracked_objects.items():
            obj_dict = obj.to_dict()
            # 注入轨迹历史
            obj_dict["trajectory"] = self.trajectory_history.get(obj_id, [])
            result[obj_id] = obj_dict
        return result

    def get_active_tracked_objects(self) -> dict:
        """获取当前活动的跟踪对象（disappeared == 0）"""
        return {
            obj_id: obj
            for obj_id, obj in self.tracked_objects.items()
            if self.disappeared.get(obj_id, 0) == 0
        }
