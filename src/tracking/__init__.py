"""
跟踪模块

包含：
- BoT-SORT（推荐）：MOT17 排名第一，精度最高
- ByteTrack（可选）：简单快速，只使用 IoU 匹配
"""

from .botsort import BoTSORTTracker, Track, KalmanFilterH, ReIDExtractor

__all__ = ['BoTSORTTracker', 'Track', 'KalmanFilterH', 'ReIDExtractor']