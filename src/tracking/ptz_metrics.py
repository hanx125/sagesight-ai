"""
PTZ 指标共享状态

用于 NorfairTracker 与 PTZ 自动跟踪进程之间的跨进程状态共享:
- autotracker_enabled: 是否启用自动跟踪
- start_time/stop_time: PTZ 移动的起止时间
- frame_time: 当前帧时间
- zoom_level/max_zoom/min_zoom: 变焦级别
- tracking_active: 跟踪是否激活
- motor_stopped: 马达是否停止
- reset: 重置运动估计器

与项目 src/cameras/ptz_service.py 配合使用:
- ptz_service.py 提供 ONVIF PTZ 手动控制基础服务
- PTZMetrics 提供跨进程共享状态,供自动跟踪逻辑使用
- PtzMotionEstimator（后续实现）读取 PTZMetrics 状态,补偿相机运动
"""

import multiprocessing as mp
from typing import Optional


class PTZMetrics:
    """
    PTZ 自动跟踪跨进程共享状态

    所有字段使用 multiprocessing 共享对象,可在 NorfairTracker 进程、
    PTZ 控制进程、事件处理进程之间共享。

    使用方式:
        ptz_metrics = PTZMetrics(autotracker_enabled=False)
        tracker = NorfairTracker(..., ptz_metrics=ptz_metrics)
        # 后续实现 autotrack 时,PTZ 控制进程读取/写入这些状态
    """

    def __init__(self, *, autotracker_enabled: bool = False):
        # 是否启用自动跟踪（0=禁用, 1=启用）
        self.autotracker_enabled = mp.Value("i", int(autotracker_enabled))

        # PTZ 移动起止时间（用于判断帧是否在 PTZ 移动期间）
        self.start_time = mp.Value("d", 0.0)
        self.stop_time = mp.Value("d", 0.0)

        # 当前帧时间
        self.frame_time = mp.Value("d", 0.0)

        # 变焦级别
        self.zoom_level = mp.Value("d", 0.0)
        self.max_zoom = mp.Value("d", 0.0)
        self.min_zoom = mp.Value("d", 0.0)

        # 跟踪状态事件
        self.tracking_active = mp.Event()
        self.motor_stopped = mp.Event()
        self.reset = mp.Event()

        # 初始状态:马达已停止
        self.motor_stopped.set()

    def is_autotracker_enabled(self) -> bool:
        """是否启用自动跟踪"""
        return bool(self.autotracker_enabled.value)

    def is_ptz_moving_at(self, frame_time: float) -> bool:
        """
        判断指定帧时间时 PTZ 是否正在移动

        用于 NorfairTracker 判断是否需要补偿相机运动。

        Args:
            frame_time: 帧时间戳

        Returns:
            bool: PTZ 是否正在移动
        """
        ptz_start_time = self.start_time.value
        ptz_stop_time = self.stop_time.value

        # start_time 为 0 表示从未移动过
        if ptz_start_time == 0.0:
            return False

        # 帧时间在移动开始之前
        if frame_time <= ptz_start_time:
            return False

        # stop_time 为 0 表示正在移动中
        if ptz_stop_time == 0.0:
            return True

        # 帧时间在移动期间
        return ptz_start_time <= frame_time <= ptz_stop_time
