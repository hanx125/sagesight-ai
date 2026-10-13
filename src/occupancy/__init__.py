"""
区域遗留物引擎（物品堆积检测 / 异常占用检测）

核心思路：配置"应保持空旷"的多边形区域，以人工拍摄的空旷基线为参照，
逐秒做背景差分，物体出现且持续存在超过时长阈值即告警，移走后恢复。

模块组成:
- baseline_manager: 基线采集/存储/加载 + 拍摄请求标记 + 运行时状态回写
- diff_analyzer:    差分计算（ROI裁剪/亮度归一化/二值化/形态学/面积占比），纯函数无状态
- occupancy_state:  区域状态机（UNCALIBRATED/MONITORING/ALERTED/INVALID）与通道级聚合

两种检测类型底层共用同一引擎，差异仅在事件类型与默认持续时长参数:
- pile_up（物品堆积检测）: 默认持续 300s 告警
- abnormal_occupancy（异常占用检测）: 默认持续 60s 告警
"""

# 占道/堆积类检测类型（detection_type 与 event_type 同名）
OCCUPANCY_DETECTION_TYPES = ('pile_up', 'abnormal_occupancy')

from src.occupancy.baseline_manager import BaselineManager
from src.occupancy.diff_analyzer import DiffAnalyzer, DiffResult, DIFF_THRESHOLD
from src.occupancy.occupancy_state import (
    ChannelOccupancyMonitor,
    ZoneOccupancyState,
    GLOBAL_CHANGE_RATIO,
    GRACE_SECONDS,
    CLEAR_CONFIRM_SECONDS,
    DEFAULT_MIN_AREA_RATIO,
    DEFAULT_DWELL,
    ZONE_ENABLE_KEY,
    ZONE_DWELL_KEY,
)

__all__ = [
    'OCCUPANCY_DETECTION_TYPES',
    'BaselineManager',
    'DiffAnalyzer',
    'DiffResult',
    'DIFF_THRESHOLD',
    'ChannelOccupancyMonitor',
    'ZoneOccupancyState',
    'GLOBAL_CHANGE_RATIO',
    'GRACE_SECONDS',
    'CLEAR_CONFIRM_SECONDS',
    'DEFAULT_MIN_AREA_RATIO',
    'DEFAULT_DWELL',
    'ZONE_ENABLE_KEY',
    'ZONE_DWELL_KEY',
]
