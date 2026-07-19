"""
跟踪模块

提供目标跟踪和 PTZ 自动跟踪功能。
"""

# 目标跟踪（BoT-SORT）
from .botsort import BoTSORTTracker

# PTZ 自动跟踪
from .autotrack import (
    AutoTrackController,
    TrackedObjectInfo,
    PTZMetrics,
    create_autotrack_controller,
)

__all__ = [
    # BoT-SORT 跟踪器
    'BoTSORTTracker',
    
    # PTZ 自动跟踪
    'AutoTrackController',
    'TrackedObjectInfo',
    'PTZMetrics',
    'create_autotrack_controller',
]