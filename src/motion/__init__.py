"""运动检测模块

独立的运动检测层，位于目标检测之前，用于优化检测性能。

架构流程：
    共享内存 (YUV帧)
        ↓ frame_manager.get()
    帧数据
        ↓ motion_detector.detect()
    运动区域
        ↓ 判断是否有运动
    ├─ 有运动：创建检测区域 → 预处理 → 目标检测
    └─ 无运动：跳过检测（优化性能）

模块结构：
    - motion_detector.py: MOG2 运动检测器
    - motion_region.py: 运动区域数据结构
    - motion_filter.py: 运动区域过滤工具
"""

from .motion_detector import MotionDetector, MotionRegion
from .motion_filter import MotionFilter

__all__ = [
    'MotionDetector',
    'MotionRegion',
    'MotionFilter',
]