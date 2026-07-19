"""
对象状态管理模块

提供对象状态跟踪和管理功能：
- 对象状态定义（静止/移动）
- 位置历史跟踪
- 状态判断（基于位置变化）
- 推理策略优化（静止对象降低推理频率）

使用场景：
- 区分静止和移动对象
- 优化推理策略（减少推理次数）
- 提供额外的跟踪线索

参考：
- Frigate TrackedObject 的 motionless_count
"""

import math
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from collections import deque


class ObjectState(Enum):
    """对象状态"""
    UNKNOWN = "unknown"  # 未知状态（刚检测到）
    STATIONARY = "stationary"  # 静止状态
    MOVING = "moving"  # 移动状态


@dataclass
class PositionRecord:
    """位置记录"""
    bbox: Tuple[int, int, int, int]  # 边界框 (x1, y1, x2, y2)
    timestamp: float  # 时间戳
    center: Tuple[float, float] = field(init=False)  # 中心点
    
    def __post_init__(self):
        """计算中心点"""
        self.center = (
            (self.bbox[0] + self.bbox[2]) / 2,
            (self.bbox[1] + self.bbox[3]) / 2
        )


@dataclass
class ObjectStateInfo:
    """对象状态信息"""
    track_id: int  # 跟踪 ID
    state: ObjectState = ObjectState.UNKNOWN  # 当前状态
    position_history: deque = field(default_factory=lambda: deque(maxlen=30))  # 位置历史
    state_change_time: float = 0.0  # 状态变化时间
    stationary_frames: int = 0  # 静止帧数
    moving_frames: int = 0  # 移动帧数
    total_frames: int = 0  # 总帧数
    
    # 状态判断参数
    position_change_threshold: float = 50.0  # 位置变化阈值（像素）
    min_history_length: int = 10  # 最小历史长度（帧）
    
    def add_position(self, bbox: Tuple[int, int, int, int]):
        """
        添加位置记录
        
        Args:
            bbox: 边界框 (x1, y1, x2, y2)
        """
        record = PositionRecord(bbox=bbox, timestamp=time.time())
        self.position_history.append(record)
        self.total_frames += 1
    
    def calculate_average_movement(self) -> float:
        """
        计算平均移动距离
        
        Returns:
            float: 平均移动距离（像素）
        """
        if len(self.position_history) < 2:
            return 0.0
        
        distances = []
        positions = list(self.position_history)
        
        for i in range(1, len(positions)):
            prev_center = positions[i-1].center
            curr_center = positions[i].center
            
            distance = math.sqrt(
                (curr_center[0] - prev_center[0])**2 + 
                (curr_center[1] - prev_center[1])**2
            )
            distances.append(distance)
        
        return sum(distances) / len(distances)
    
    def update_state(self) -> ObjectState:
        """
        更新对象状态
        
        Returns:
            ObjectState: 当前状态
        """
        # 需要足够的历史记录才能判断状态
        if len(self.position_history) < self.min_history_length:
            self.state = ObjectState.UNKNOWN
            return self.state
        
        # 计算平均移动距离
        avg_movement = self.calculate_average_movement()
        
        # 判断状态
        previous_state = self.state
        
        if avg_movement < self.position_change_threshold:
            self.state = ObjectState.STATIONARY
            self.stationary_frames += 1
        else:
            self.state = ObjectState.MOVING
            self.moving_frames += 1
        
        # 记录状态变化时间
        if previous_state != self.state:
            self.state_change_time = time.time()
        
        return self.state
    
    def get_state_duration(self) -> float:
        """
        获取当前状态持续时间
        
        Returns:
            float: 持续时间（秒）
        """
        if self.state_change_time == 0:
            return 0.0
        return time.time() - self.state_change_time
    
    def get_stats(self) -> dict:
        """
        获取统计信息
        
        Returns:
            dict: 统计信息
        """
        return {
            "track_id": self.track_id,
            "state": self.state.value,
            "state_duration": self.get_state_duration(),
            "stationary_frames": self.stationary_frames,
            "moving_frames": self.moving_frames,
            "total_frames": self.total_frames,
            "avg_movement": self.calculate_average_movement(),
            "position_history_length": len(self.position_history)
        }


class ObjectStateManager:
    """
    对象状态管理器
    
    功能：
    - 管理多个对象的状态
    - 跟踪位置历史
    - 判断静止/移动状态
    - 提供推理优化建议
    """
    
    def __init__(
        self,
        position_change_threshold: float = 50.0,
        min_history_length: int = 10,
        stationary_skip_frames: int = 5
    ):
        """
        初始化对象状态管理器
        
        Args:
            position_change_threshold: 位置变化阈值（像素）
            min_history_length: 最小历史长度（帧）
            stationary_skip_frames: 静止对象跳过的帧数
        """
        self.objects: Dict[int, ObjectStateInfo] = {}  # track_id -> ObjectStateInfo
        self.position_change_threshold = position_change_threshold
        self.min_history_length = min_history_length
        self.stationary_skip_frames = stationary_skip_frames
        
        # 帧计数器
        self.frame_count = 0
        
        print(f"[ObjectStateManager] 初始化完成")
        print(f"[ObjectStateManager]   位置变化阈值: {position_change_threshold} 像素")
        print(f"[ObjectStateManager]   最小历史长度: {min_history_length} 帧")
        print(f"[ObjectStateManager]   静止对象跳过: {stationary_skip_frames} 帧")
    
    def update_object(self, track_id: int, bbox: Tuple[int, int, int, int]) -> ObjectState:
        """
        更新对象状态
        
        Args:
            track_id: 跟踪 ID
            bbox: 边界框 (x1, y1, x2, y2)
            
        Returns:
            ObjectState: 当前状态
        """
        # 创建或获取对象状态信息
        if track_id not in self.objects:
            self.objects[track_id] = ObjectStateInfo(
                track_id=track_id,
                position_change_threshold=self.position_change_threshold,
                min_history_length=self.min_history_length
            )
        
        obj_info = self.objects[track_id]
        
        # 添加位置记录
        obj_info.add_position(bbox)
        
        # 更新状态
        state = obj_info.update_state()
        
        return state
    
    def get_object_state(self, track_id: int) -> ObjectState:
        """
        获取对象状态
        
        Args:
            track_id: 跟踪 ID
            
        Returns:
            ObjectState: 对象状态
        """
        if track_id not in self.objects:
            return ObjectState.UNKNOWN
        return self.objects[track_id].state
    
    def is_stationary(self, track_id: int) -> bool:
        """
        检测对象是否静止
        
        Args:
            track_id: 跟踪 ID
            
        Returns:
            bool: 是否静止
        """
        return self.get_object_state(track_id) == ObjectState.STATIONARY
    
    def is_moving(self, track_id: int) -> bool:
        """
        检测对象是否移动
        
        Args:
            track_id: 跟踪 ID
            
        Returns:
            bool: 是否移动
        """
        return self.get_object_state(track_id) == ObjectState.MOVING
    
    def should_skip_inference(self, track_id: int) -> bool:
        """
        判断是否应该跳过推理（推理优化）
        
        策略：
        - 静止对象：每 N 帧推理一次
        - 移动对象：每帧推理
        
        参考 Frigate 的 motionless_count 逻辑：
        - 静止对象可以降低推理频率，节省计算资源
        
        Args:
            track_id: 跟踪 ID
            
        Returns:
            bool: 是否跳过推理
        """
        if track_id not in self.objects:
            return False
        
        obj_info = self.objects[track_id]
        
        # 移动对象：不跳过
        if obj_info.state == ObjectState.MOVING:
            return False
        
        # 静止对象：每 N 帧推理一次
        if obj_info.state == ObjectState.STATIONARY:
            # 计算自上次推理以来的帧数
            frames_since_last_inference = obj_info.total_frames % self.stationary_skip_frames
            return frames_since_last_inference != 0
        
        return False
    
    def remove_object(self, track_id: int):
        """
        移除对象（跟踪结束时调用）
        
        Args:
            track_id: 跟踪 ID
        """
        if track_id in self.objects:
            del self.objects[track_id]
    
    def clear_all_objects(self):
        """清除所有对象"""
        self.objects.clear()
    
    def get_all_states(self) -> Dict[int, ObjectState]:
        """
        获取所有对象的状态
        
        Returns:
            Dict[int, ObjectState]: 所有对象的状态
        """
        return {track_id: info.state for track_id, info in self.objects.items()}
    
    def get_stationary_objects(self) -> List[int]:
        """
        获取所有静止对象的 ID
        
        Returns:
            List[int]: 静止对象 ID 列表
        """
        return [
            track_id for track_id, info in self.objects.items()
            if info.state == ObjectState.STATIONARY
        ]
    
    def get_moving_objects(self) -> List[int]:
        """
        获取所有移动对象的 ID
        
        Returns:
            List[int]: 移动对象 ID 列表
        """
        return [
            track_id for track_id, info in self.objects.items()
            if info.state == ObjectState.MOVING
        ]
    
    def get_stats(self) -> dict:
        """
        获取统计信息
        
        Returns:
            dict: 统计信息
        """
        stationary_count = len(self.get_stationary_objects())
        moving_count = len(self.get_moving_objects())
        unknown_count = len(self.objects) - stationary_count - moving_count
        
        return {
            "total_objects": len(self.objects),
            "stationary_objects": stationary_count,
            "moving_objects": moving_count,
            "unknown_objects": unknown_count,
            "frame_count": self.frame_count
        }
    
    def get_object_stats(self, track_id: int) -> Optional[dict]:
        """
        获取单个对象的统计信息
        
        Args:
            track_id: 跟踪 ID
            
        Returns:
            Optional[dict]: 统计信息（如果对象存在）
        """
        if track_id not in self.objects:
            return None
        return self.objects[track_id].get_stats()