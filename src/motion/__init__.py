"""运动检测模块

基于帧差法实现，CPU 占用比 MOG2 低 5 倍

架构流程：
    共享内存 (YUV帧)
        ↓ read_frame_i420
    (header, y_plane, u_plane, v_plane)
        ↓ motion_detector.detect(y_plane)
    motion_boxes（运动区域列表）
        ↓ 生成 regions
    检测区域（缩小检测范围）
        ↓ 对每个 region 同步检测
    detections

模块结构：
    - improved_motion.py: 帧差法运动检测器（ImprovedMotionDetector）
"""

from .improved_motion import ImprovedMotionDetector, MotionRegion

__all__ = [
    'ImprovedMotionDetector',
    'MotionRegion',
]