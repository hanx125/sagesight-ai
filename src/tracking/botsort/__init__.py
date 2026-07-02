"""
BoT-SORT 多目标跟踪模块

基于 ByteTrack + DeepSORT + 改进卡尔曼滤波 + ReID 特征提取
MOT17 排名第一：MOTA=80.5, IDF1=80.2, HOTA=65.0

架构：
- BoTSORTTracker: 主跟踪器（三阶段匹配）
- KalmanFilterH: 改进的卡尔曼滤波（h_ratio 状态向量）
- ReIDExtractor: ReID 特征提取器（可选）
- Track: 轨迹类（包含外观特征）

参考：
- https://github.com/NirAharon/BoT-SORT
- https://github.com/ifzhang/ByteTrack
- https://github.com/nwojke/deep_sort
"""

from .botsort_tracker import BoTSORTTracker
from .kalman_filter_h import KalmanFilterH
from .track import Track
from .reid_extractor import ReIDExtractor

__all__ = ['BoTSORTTracker', 'KalmanFilterH', 'Track', 'ReIDExtractor']