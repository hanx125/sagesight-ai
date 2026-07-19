"""
PTZ 自动跟踪模块

实现基于目标检测的 PTZ 自动跟踪功能：
- 运动估计（norfair）
- PTZ 控制策略
- 自动跟踪目标选择
- PTZ 移动管理

使用场景：
- 固定 PTZ 摄像机自动跟踪目标
- 智能监控区域覆盖
- 目标跟随和变焦
"""

import logging
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from multiprocessing import Event as MpEvent

import cv2
import numpy as np

# 尝试导入 norfair（运动估计库）
try:
    from norfair.camera_motion import (
        HomographyTransformationGetter,
        MotionEstimator,
        TranslationTransformationGetter,
    )
    NORFAIR_AVAILABLE = True
except ImportError:
    NORFAIR_AVAILABLE = False
    logging.warning("norfair库未安装，运动估计功能不可用。请安装: pip install norfair")

# 导入项目内部模块
from src.cameras.ptz_service import PTZController, PTZCapabilities
from src.cameras.onvif_client import OnvifCameraClient

logger = logging.getLogger(__name__)


# ========================================
# 常量定义（PTZ 自动跟踪参数）
# ========================================

AUTOTRACKING_MAX_AREA_RATIO = 0.6          # 检测框最大面积比例
AUTOTRACKING_MOTION_MIN_DISTANCE = 20      # 运动估计最小距离
AUTOTRACKING_MOTION_MAX_POINTS = 500       # 运动估计最大点数
AUTOTRACKING_MAX_MOVE_METRICS = 500        # 最大移动指标数
AUTOTRACKING_ZOOM_OUT_HYSTERESIS = 1.1     # 变焦出滞后
AUTOTRACKING_ZOOM_IN_HYSTERESIS = 0.95     # 变焦入滞后
AUTOTRACKING_ZOOM_EDGE_THRESHOLD = 0.05    # 变焦边缘阈值


# ========================================
# 数据类定义
# ========================================

@dataclass
class PTZMetrics:
    """PTZ 指标数据类"""
    start_time: float = 0.0             # PTZ 开始移动时间
    stop_time: float = 0.0              # PTZ 停止移动时间
    reset: MpEvent = field(default_factory=MpEvent)  # 重置事件


@dataclass
class TrackedObjectInfo:
    """跟踪对象信息"""
    track_id: int                       # 跟踪 ID
    label: str                          # 类别标签
    confidence: float                   # 置信度
    bbox: Tuple[int, int, int, int]    # 边界框 (x1, y1, x2, y2)
    area: int                           # 面积
    center: Tuple[float, float]         # 中心点 (cx, cy)


# ========================================
# 辅助函数
# ========================================

def ptz_moving_at_frame_time(frame_time: float, ptz_start_time: float, ptz_stop_time: float) -> bool:
    """
    判断 PTZ 在帧时间是否正在移动
    
    Args:
        frame_time: 帧时间戳
        ptz_start_time: PTZ 开始移动时间
        ptz_stop_time: PTZ 停止移动时间
        
    Returns:
        PTZ 是否正在移动
    """
    return (ptz_start_time != 0.0 and frame_time > ptz_start_time) and (
        ptz_stop_time == 0.0 or (ptz_start_time <= frame_time <= ptz_stop_time)
    )


# ========================================
# PTZ 运动估计器
# ========================================

class PtzMotionEstimator:
    """PTZ 运动估计器（基于 norfair）"""
    
    def __init__(self, camera_name: str, ptz_metrics: PTZMetrics, enable_zoom: bool = False):
        """
        初始化运动估计器
        
        Args:
            camera_name: 摄像机名称
            ptz_metrics: PTZ 指标
            enable_zoom: 是否启用变焦
        """
        self.camera_name = camera_name
        self.ptz_metrics = ptz_metrics
        self.enable_zoom = enable_zoom
        
        # norfair 运动估计器
        self.norfair_motion_estimator: Optional[MotionEstimator] = None
        
        # 坐标变换
        self.coord_transformations = None
        
        # 帧管理器（简化版）
        self.frame_cache: Dict[float, np.ndarray] = {}
        
        logger.debug(f"{camera_name}: Motion estimator initialized")
    
    def reset_estimator(self):
        """重置运动估计器"""
        if not NORFAIR_AVAILABLE:
            logger.warning(f"{self.camera_name}: norfair 不可用，无法重置运动估计器")
            return
        
        # homography 用于变焦，translation 用于平移/倾斜
        if self.enable_zoom:
            transformation_type = HomographyTransformationGetter()
            logger.debug(f"{self.camera_name}: Motion estimator reset - homography (zoom)")
        else:
            transformation_type = TranslationTransformationGetter()
            logger.debug(f"{self.camera_name}: Motion estimator reset - translation (pan/tilt)")
        
        self.norfair_motion_estimator = MotionEstimator(
            transformations_getter=transformation_type,
            min_distance=AUTOTRACKING_MOTION_MIN_DISTANCE,
            max_points=AUTOTRACKING_MOTION_MAX_POINTS,
        )
        
        self.coord_transformations = None
    
    def estimate_motion(
        self,
        detections: List[TrackedObjectInfo],
        frame: np.ndarray,
        frame_time: float
    ) -> Optional[np.ndarray]:
        """
        估计 PTZ 运动
        
        Args:
            detections: 检测结果列表
            frame: 当前帧（BGR 格式）
            frame_time: 帧时间戳
            
        Returns:
            坐标变换矩阵（如果有效）
        """
        if not NORFAIR_AVAILABLE or self.norfair_motion_estimator is None:
            return None
        
        # 检查是否需要重置
        if self.ptz_metrics.reset.is_set():
            self.reset_estimator()
            self.ptz_metrics.reset.clear()
        
        # 检查 PTZ 是否在移动
        if not ptz_moving_at_frame_time(
            frame_time,
            self.ptz_metrics.start_time,
            self.ptz_metrics.stop_time
        ):
            return None
        
        # 转换检测格式（简化版）
        norfair_detections = []
        for det in detections:
            # norfair 需要的点格式：[[x1, y1], [x2, y2], ...]
            points = np.array([
                [det.bbox[0], det.bbox[1]],  # 左上
                [det.bbox[2], det.bbox[3]],  # 右下
            ])
            norfair_detections.append(points)
        
        # 估计运动
        try:
            coord_transformations = self.norfair_motion_estimator.update(
                norfair_detections,
                frame
            )
            
            if coord_transformations is not None:
                self.coord_transformations = coord_transformations
                logger.debug(f"{self.camera_name}: Motion estimated at frame_time={frame_time}")
            
            return coord_transformations
            
        except Exception as e:
            logger.error(f"{self.camera_name}: Motion estimation failed: {e}")
            return None


# ========================================
# 自动跟踪控制器
# ========================================

class AutoTrackController:
    """PTZ 自动跟踪控制器"""
    
    def __init__(
        self,
        camera_name: str,
        ptz_controller: PTZController,
        enable_zoom: bool = False,
        track_classes: Optional[List[str]] = None,
        min_confidence: float = 0.5,
        max_area_ratio: float = AUTOTRACKING_MAX_AREA_RATIO,
    ):
        """
        初始化自动跟踪控制器
        
        Args:
            camera_name: 摄像机名称
            ptz_controller: PTZ 控制器
            enable_zoom: 是否启用变焦
            track_classes: 跟踪类别列表（None 表示所有类别）
            min_confidence: 最小置信度
            max_area_ratio: 检测框最大面积比例
        """
        self.camera_name = camera_name
        self.ptz_controller = ptz_controller
        self.enable_zoom = enable_zoom
        self.track_classes = track_classes or []
        self.min_confidence = min_confidence
        self.max_area_ratio = max_area_ratio
        
        # PTZ 指标
        self.ptz_metrics = PTZMetrics()
        
        # 运动估计器
        self.motion_estimator = PtzMotionEstimator(
            camera_name,
            self.ptz_metrics,
            enable_zoom
        )
        
        # 跟踪状态
        self.current_track_id: Optional[int] = None
        self.track_history: deque = deque(maxlen=30)
        
        # PTZ 状态
        self.is_tracking = False
        self.last_move_time = 0.0
        
        logger.info(f"{camera_name}: AutoTrack controller initialized (zoom={enable_zoom})")
    
    def select_target(self, detections: List[TrackedObjectInfo]) -> Optional[TrackedObjectInfo]:
        """
        选择跟踪目标
        
        策略：
        1. 优先跟踪置信度最高的对象
        2. 过滤面积过大的对象（避免 PTZ 频繁移动）
        3. 优先跟踪指定类别的对象
        
        Args:
            detections: 检测结果列表
            
        Returns:
            选中的跟踪目标（如果没有则返回 None）
        """
        if not detections:
            return None
        
        # 过滤检测结果
        valid_detections = []
        for det in detections:
            # 过滤置信度
            if det.confidence < self.min_confidence:
                continue
            
            # 过滤面积（避免过大对象）
            frame_area = 640 * 640  # 假设帧尺寸（需要从实际帧获取）
            area_ratio = det.area / frame_area
            if area_ratio > self.max_area_ratio:
                continue
            
            # 过滤类别（如果指定了跟踪类别）
            if self.track_classes and det.label not in self.track_classes:
                continue
            
            valid_detections.append(det)
        
        if not valid_detections:
            return None
        
        # 选择策略：选择置信度最高的对象
        best_detection = max(valid_detections, key=lambda d: d.confidence)
        
        return best_detection
    
    def calculate_ptz_movement(
        self,
        target: TrackedObjectInfo,
        frame_shape: Tuple[int, int]
    ) -> Tuple[float, float, float]:
        """
        计算 PTZ 移动量
        
        Args:
            target: 跟踪目标
            frame_shape: 帧尺寸 (height, width)
            
        Returns:
            PTZ 移动量 (pan, tilt, zoom)
        """
        height, width = frame_shape
        
        # 计算目标中心点相对于画面中心点的偏移
        frame_center_x = width / 2
        frame_center_y = height / 2
        
        # 目标中心点
        target_center_x = target.center[0]
        target_center_y = target.center[1]
        
        # 计算偏移量（归一化到 [-1, 1]）
        pan_offset = (target_center_x - frame_center_x) / frame_center_x
        tilt_offset = (target_center_y - frame_center_y) / frame_center_y
        
        # 计算变焦量（基于目标面积）
        zoom = 0.0
        if self.enable_zoom:
            target_area = target.area
            frame_area = height * width
            area_ratio = target_area / frame_area
            
            # 如果目标太小，放大；如果太大，缩小
            if area_ratio < 0.1:  # 目标太小
                zoom = 0.2
            elif area_ratio > 0.4:  # 目标太大
                zoom = -0.2
        
        # 缩放 PTZ 移动量（根据经验调整）
        pan = pan_offset * 0.3   # Pan 移动量（相对值）
        tilt = tilt_offset * 0.3  # Tilt 移动量（相对值）
        
        return pan, tilt, zoom
    
    def update(
        self,
        detections: List[TrackedObjectInfo],
        frame: np.ndarray,
        frame_time: float
    ):
        """
        更新自动跟踪状态
        
        Args:
            detections: 检测结果列表
            frame: 当前帧
            frame_time: 帧时间戳
        """
        # 选择跟踪目标
        target = self.select_target(detections)
        
        if target is None:
            # 没有目标，停止跟踪
            if self.is_tracking:
                self.ptz_controller.stop()
                self.is_tracking = False
                self.current_track_id = None
                logger.debug(f"{self.camera_name}: Tracking stopped (no target)")
            return
        
        # 检查是否是新目标
        if target.track_id != self.current_track_id:
            logger.info(f"{self.camera_name}: Tracking new target: {target.track_id} ({target.label})")
            self.current_track_id = target.track_id
        
        # 计算 PTZ 移动量
        pan, tilt, zoom = self.calculate_ptz_movement(target, frame.shape)
        
        # 执行 PTZ 移动
        if any([pan, tilt, zoom]):
            # 更新 PTZ 指标
            self.ptz_metrics.start_time = frame_time
            
            # 执行移动
            self.ptz_controller.move([pan, tilt, zoom])
            
            # 更新停止时间（异步更新，由 PTZ 控制器管理）
            self.ptz_metrics.stop_time = frame_time + 0.5  # 估计移动时间
            
            self.is_tracking = True
            self.last_move_time = frame_time
            
            logger.debug(
                f"{self.camera_name}: PTZ move: pan={pan:.3f}, tilt={tilt:.3f}, zoom={zoom:.3f}"
            )
        
        # 记录跟踪历史
        self.track_history.append({
            'track_id': target.track_id,
            'bbox': target.bbox,
            'time': frame_time
        })
    
    def stop_tracking(self):
        """停止自动跟踪"""
        if self.is_tracking:
            self.ptz_controller.stop()
            self.is_tracking = False
            self.current_track_id = None
            logger.info(f"{self.camera_name}: AutoTrack stopped")
    
    def reset(self):
        """重置跟踪状态"""
        self.stop_tracking()
        self.track_history.clear()
        self.ptz_metrics.reset.set()
        logger.info(f"{self.camera_name}: AutoTrack reset")


# ========================================
# 工厂函数
# ========================================

def create_autotrack_controller(
    camera_name: str,
    onvif_client: OnvifCameraClient,
    media_token: str,
    ptz_config_token: str,
    enable_zoom: bool = False,
    track_classes: Optional[List[str]] = None,
) -> Optional[AutoTrackController]:
    """
    创建自动跟踪控制器
    
    Args:
        camera_name: 摄像机名称
        onvif_client: ONVIF 客户端
        media_token: Media Profile 令牌
        ptz_config_token: PTZ 配置令牌
        enable_zoom: 是否启用变焦
        track_classes: 跟踪类别列表
        
    Returns:
        自动跟踪控制器（如果创建失败则返回 None）
    """
    try:
        # 创建 PTZ 控制器
        from src.cameras.ptz_service import get_ptz_service_manager
        
        ptz_manager = get_ptz_service_manager()
        ptz_controller = ptz_manager.create_ptz_controller(
            onvif_client._camera,
            media_token,
            ptz_config_token
        )
        
        if ptz_controller is None:
            logger.error(f"{camera_name}: Failed to create PTZ controller")
            return None
        
        # 创建自动跟踪控制器
        autotrack = AutoTrackController(
            camera_name,
            ptz_controller,
            enable_zoom,
            track_classes
        )
        
        return autotrack
        
    except Exception as e:
        logger.error(f"{camera_name}: Failed to create AutoTrack controller: {e}")
        return None