"""
轨迹类（BoT-SORT）

包含：
- 卡尔曼滤波状态
- 外观特征（ReID embedding）
- 轨迹历史
- 轨迹生命周期管理
"""

import numpy as np
from collections import deque
from typing import Optional
from .kalman_filter_h import KalmanFilterH
import logging

logger = logging.getLogger('DetectorWorker')


class Track:
    """轨迹类"""
    
    # 轨迹状态
    TENTATIVE = 0   # 待确认（新轨迹）
    CONFIRMED = 1   # 已确认
    DELETED = 2     # 已删除
    
    def __init__(self, 
                 bbox: np.ndarray,
                 score: float,
                 class_id: int,
                 track_id: int,
                 embedding: Optional[np.ndarray] = None,
                 max_history_length: int = 30,
                 max_age: int = 30,
                 n_init: int = 3):
        """
        初始化轨迹
        
        Args:
            bbox: 边界框 [cx, cy, w, h]（中心坐标 + 宽高）
            score: 检测分数
            class_id: 类别 ID
            track_id: 轨迹 ID
            embedding: 外观特征向量（可选）
            max_history_length: 最大历史长度
            max_age: 最大丢失帧数
            n_init: 新轨迹确认帧数
        """
        # 基本信息
        self.track_id = track_id
        self.class_id = class_id
        self.score = score
        self.max_age = max_age
        self.n_init = n_init
        
        # 卡尔曼滤波
        self.kf = KalmanFilterH(bbox)
        
        # 外观特征
        self.embedding = embedding.copy() if embedding is not None else None
        self.embedding_history = deque(maxlen=100)  # 外观特征历史
        
        if embedding is not None:
            self.embedding_history.append(embedding.copy())
        
        # 轨迹历史（用于绘制轨迹线）
        self.history = deque(maxlen=max_history_length)
        self.history.append(bbox.copy())  # 记录初始位置
        
        # 轨迹状态
        self.state = Track.TENTATIVE
        self.hits = 1                     # 连续匹配次数
        self.age = 0                      # 轨迹年龄（总帧数）
        self.consecutive_misses = 0       # 连续丢失次数
        
        # 初始尺寸（用于尺寸一致性检查）
        self.initial_w = bbox[2]
        self.initial_h = bbox[3]
    
    def predict(self) -> np.ndarray:
        """
        预测下一帧位置
        
        Returns:
            预测的边界框 [cx, cy, w, h]
        """
        self.age += 1
        
        # 使用卡尔曼滤波预测
        predicted_bbox = self.kf.predict()
        
        # 限制速度（防止预测漂移）
        velocity = self.kf.get_velocity()
        max_velocity = 150  # 最大速度（像素/帧）
        
        # 动态速度限制（基于历史速度）
        recent_velocity = np.abs(velocity)
        adaptive_max_velocity = np.maximum(recent_velocity * 1.5, max_velocity)
        
        # 限制速度
        self.kf.state[4:8] = np.clip(
            self.kf.state[4:8],
            -adaptive_max_velocity,
            adaptive_max_velocity
        )
        
        # 重新计算预测位置
        predicted_bbox = self.kf.get_state()
        
        # 限制宽高范围（防止预测框异常）
        max_w = self.initial_w * 2.0
        max_h = self.initial_h * 2.0
        min_w = self.initial_w * 0.5
        min_h = self.initial_h * 0.5
        
        predicted_bbox[2] = np.clip(predicted_bbox[2], min_w, max_w)
        predicted_bbox[3] = np.clip(predicted_bbox[3], min_h, max_h)
        
        # 确保最小值
        predicted_bbox[2] = max(predicted_bbox[2], 10.0)
        predicted_bbox[3] = max(predicted_bbox[3], 10.0)
        
        return predicted_bbox
    
    def update(self, 
               bbox: np.ndarray,
               score: float,
               embedding: Optional[np.ndarray] = None) -> np.ndarray:
        """
        更新轨迹（匹配成功）
        
        Args:
            bbox: 检测边界框 [cx, cy, w, h]
            score: 检测分数
            embedding: 外观特征向量（可选）
        
        Returns:
            更新后的边界框 [cx, cy, w, h]
        """
        # 重置丢失计数
        self.hits += 1
        self.consecutive_misses = 0
        
        # 更新分数
        self.score = score
        
        # 更新卡尔曼滤波
        updated_bbox = self.kf.update(bbox)
        
        # 更新外观特征
        if embedding is not None:
            self.embedding = embedding.copy()
            self.embedding_history.append(embedding.copy())
        
        # 记录历史（使用原始检测位置，不使用卡尔曼滤波预测）
        self.history.append(bbox.copy())
        
        # 确认轨迹（使用配置的 n_init 参数）
        if self.state == Track.TENTATIVE and self.hits >= self.n_init:
            self.state = Track.CONFIRMED
        
        return updated_bbox
    
    def mark_missed(self):
        """标记为丢失"""
        self.consecutive_misses += 1
        
        # 超过最大丢失次数，标记为删除（使用配置的 max_age）
        if self.consecutive_misses > self.max_age:
            self.state = Track.DELETED
    
    def get_bbox(self) -> np.ndarray:
        """
        获取当前边界框
        
        Returns:
            边界框 [cx, cy, w, h]
        """
        return self.kf.get_state()
    
    def get_bbox_xyxy(self) -> np.ndarray:
        """
        获取边界框 [x1, y1, x2, y2]
        
        Returns:
            边界框 [x1, y1, x2, y2]
        """
        cx, cy, w, h = self.kf.get_state()
        x1 = cx - w / 2
        y1 = cy - h / 2
        x2 = cx + w / 2
        y2 = cy + h / 2
        
        return np.array([x1, y1, x2, y2])
    
    def get_avg_embedding(self) -> Optional[np.ndarray]:
        """
        获取平均外观特征
        
        Returns:
            平均外观特征向量
        """
        if len(self.embedding_history) == 0:
            return None
        
        embeddings = np.array(list(self.embedding_history))
        return np.mean(embeddings, axis=0)
    
    def is_confirmed(self) -> bool:
        """是否已确认"""
        return self.state == Track.CONFIRMED
    
    def is_deleted(self) -> bool:
        """是否已删除"""
        return self.state == Track.DELETED
    
    def is_tentative(self) -> bool:
        """是否待确认"""
        return self.state == Track.TENTATIVE