"""
跟踪模块

提供目标跟踪功能，基于 Norfair 框架实现。

主要组件：
1. NorfairTracker: Norfair 跟踪器（替代旧的 BoT-SORT）
2. TrackedObject: 跟踪对象状态机（entering→active→stationary→leaving→ended）
3. PTZMetrics: PTZ 共享状态（用于自动跟踪）
"""

from .norfair_tracker import NorfairTracker
from .tracked_object import TrackedObject
from .ptz_metrics import PTZMetrics

__all__ = [
    'NorfairTracker',
    'TrackedObject',
    'PTZMetrics',
]
