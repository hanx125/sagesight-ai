"""
事件维护线程模块

提供事件生命周期管理功能：
- 独立线程管理事件
- 清理过期事件
- 更新事件状态
- 数据库持久化（可选）

使用场景：
- 异步管理事件生命周期
- 减少主线程负担
- 保证事件一致性

参考：
- Frigate EventMaintainer
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable
from datetime import datetime


@dataclass
class EventData:
    """事件数据"""
    event_id: str  # 事件 ID
    event_type: str  # 事件类型
    camera_id: str  # 摄像机 ID
    track_id: int  # 跟踪 ID
    
    # 事件数据
    class_id: int = 0  # 类别 ID
    class_name: str = ""  # 类别名称
    confidence: float = 0.0  # 置信度
    bbox: tuple = (0, 0, 0, 0)  # 边界框
    
    # 时间信息
    start_time: float = field(default_factory=time.time)  # 开始时间
    end_time: Optional[float] = None  # 结束时间
    last_update: float = field(default_factory=time.time)  # 最后更新时间
    
    # 区域信息（Zone）
    zones: list = field(default_factory=list)  # 所在区域
    entered_zones: list = field(default_factory=list)  # 进入过的区域
    
    # 状态信息
    is_active: bool = True  # 是否活跃
    is_finalized: bool = False  # 是否已结束
    
    # 媒体信息（可选）
    has_snapshot: bool = False  # 是否有快照
    has_clip: bool = False  # 是否有录像
    snapshot_path: Optional[str] = None  # 快照路径
    clip_path: Optional[str] = None  # 录像路径
    
    # 扩展数据
    extra_data: dict = field(default_factory=dict)  # 额外数据
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "camera_id": self.camera_id,
            "track_id": self.track_id,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "bbox": self.bbox,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "last_update": self.last_update,
            "zones": self.zones,
            "entered_zones": self.entered_zones,
            "is_active": self.is_active,
            "is_finalized": self.is_finalized,
            "has_snapshot": self.has_snapshot,
            "has_clip": self.has_clip,
            "snapshot_path": self.snapshot_path,
            "clip_path": self.clip_path,
            "extra_data": self.extra_data
        }
    
    def get_duration(self) -> float:
        """获取事件持续时间（秒）"""
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time


class EventMaintainer(threading.Thread):
    """
    事件维护线程
    
    功能：
    - 管理活跃事件
    - 清理过期事件
    - 更新事件状态
    - 同步到数据库（可选）
    """
    
    def __init__(
        self,
        event_timeout: float = 300.0,  # 事件超时时间（秒）
        cleanup_interval: float = 60.0,  # 清理间隔（秒）
        max_events: int = 1000,  # 最大事件数量
        on_event_timeout: Optional[Callable] = None  # 事件超时回调
    ):
        """
        初始化事件维护线程
        
        Args:
            event_timeout: 事件超时时间（秒）
            cleanup_interval: 清理间隔（秒）
            max_events: 最大事件数量
            on_event_timeout: 事件超时回调函数
        """
        super().__init__(daemon=True)
        
        self.event_timeout = event_timeout
        self.cleanup_interval = cleanup_interval
        self.max_events = max_events
        self.on_event_timeout = on_event_timeout
        
        # 事件存储
        self.events: Dict[str, EventData] = {}  # event_id -> EventData
        self.events_lock = threading.Lock()  # 线程锁
        
        # 控制标志
        self.stop_event = threading.Event()
        
        # 统计信息
        self.stats = {
            "total_events": 0,
            "active_events": 0,
            "finalized_events": 0,
            "timeout_events": 0,
            "cleanup_count": 0
        }
        
        print(f"[EventMaintainer] 初始化完成")
        print(f"[EventMaintainer]   事件超时: {event_timeout} 秒")
        print(f"[EventMaintainer]   清理间隔: {cleanup_interval} 秒")
        print(f"[EventMaintainer]   最大事件: {max_events}")
    
    def run(self):
        """主循环"""
        print("[EventMaintainer] 线程启动")
        
        while not self.stop_event.is_set():
            try:
                # 1. 清理过期事件
                self._cleanup_expired_events()
                
                # 2. 更新事件状态
                self._update_event_states()
                
                # 3. 同步到数据库（可选）
                # self._sync_to_database()
                
                # 等待下一次清理
                self.stop_event.wait(self.cleanup_interval)
                
            except Exception as e:
                print(f"[EventMaintainer] 错误: {e}")
                import traceback
                traceback.print_exc()
        
        print("[EventMaintainer] 线程停止")
    
    def add_event(self, event_data: EventData) -> bool:
        """
        添加事件
        
        Args:
            event_data: 事件数据
            
        Returns:
            bool: 是否添加成功
        """
        with self.events_lock:
            # 检查是否超过最大事件数
            if len(self.events) >= self.max_events:
                print(f"[EventMaintainer] 警告：事件数量超过上限 {self.max_events}")
                return False
            
            # 添加事件
            self.events[event_data.event_id] = event_data
            
            # 更新统计
            self.stats["total_events"] += 1
            if event_data.is_active:
                self.stats["active_events"] += 1
            
            return True
    
    def update_event(self, event_id: str, updates: dict) -> bool:
        """
        更新事件
        
        Args:
            event_id: 事件 ID
            updates: 更新数据
            
        Returns:
            bool: 是否更新成功
        """
        with self.events_lock:
            if event_id not in self.events:
                return False
            
            event = self.events[event_id]
            
            # 更新字段
            for key, value in updates.items():
                if hasattr(event, key):
                    setattr(event, key, value)
            
            # 更新最后更新时间
            event.last_update = time.time()
            
            return True
    
    def finalize_event(self, event_id: str) -> bool:
        """
        结束事件
        
        Args:
            event_id: 事件 ID
            
        Returns:
            bool: 是否结束成功
        """
        with self.events_lock:
            if event_id not in self.events:
                return False
            
            event = self.events[event_id]
            
            # 结束事件
            event.is_active = False
            event.is_finalized = True
            event.end_time = time.time()
            
            # 更新统计
            self.stats["active_events"] -= 1
            self.stats["finalized_events"] += 1
            
            return True
    
    def remove_event(self, event_id: str) -> bool:
        """
        移除事件
        
        Args:
            event_id: 事件 ID
            
        Returns:
            bool: 是否移除成功
        """
        with self.events_lock:
            if event_id not in self.events:
                return False
            
            event = self.events[event_id]
            
            # 更新统计
            if event.is_active:
                self.stats["active_events"] -= 1
            if event.is_finalized:
                self.stats["finalized_events"] -= 1
            
            # 移除事件
            del self.events[event_id]
            
            return True
    
    def get_event(self, event_id: str) -> Optional[EventData]:
        """
        获取事件
        
        Args:
            event_id: 事件 ID
            
        Returns:
            Optional[EventData]: 事件数据（如果存在）
        """
        with self.events_lock:
            return self.events.get(event_id)
    
    def get_active_events(self) -> Dict[str, EventData]:
        """
        获取所有活跃事件
        
        Returns:
            Dict[str, EventData]: 活跃事件字典
        """
        with self.events_lock:
            return {
                event_id: event for event_id, event in self.events.items()
                if event.is_active
            }
    
    def get_finalized_events(self) -> Dict[str, EventData]:
        """
        获取所有已结束事件
        
        Returns:
            Dict[str, EventData]: 已结束事件字典
        """
        with self.events_lock:
            return {
                event_id: event for event_id, event in self.events.items()
                if event.is_finalized
            }
    
    def _cleanup_expired_events(self):
        """清理过期事件"""
        current_time = time.time()
        expired_events = []
        
        with self.events_lock:
            for event_id, event in self.events.items():
                # 检查是否超时（活跃事件）
                if event.is_active:
                    if current_time - event.last_update > self.event_timeout:
                        expired_events.append(event_id)
                
                # 检查是否需要清理已结束事件（保留一段时间）
                if event.is_finalized:
                    if current_time - event.end_time > self.event_timeout:
                        expired_events.append(event_id)
        
        # 处理过期事件
        for event_id in expired_events:
            event = self.get_event(event_id)
            
            if event:
                print(f"[EventMaintainer] 清理过期事件: {event_id}")
                
                # 调用超时回调
                if self.on_event_timeout:
                    try:
                        self.on_event_timeout(event)
                    except Exception as e:
                        print(f"[EventMaintainer] 超时回调失败: {e}")
                
                # 强制结束活跃事件
                if event.is_active:
                    self.finalize_event(event_id)
                    self.stats["timeout_events"] += 1
                
                # 移除事件
                self.remove_event(event_id)
                
                # 更新统计
                self.stats["cleanup_count"] += 1
    
    def _update_event_states(self):
        """更新事件状态"""
        # 可以添加自定义的状态更新逻辑
        pass
    
    def stop(self):
        """停止线程"""
        print("[EventMaintainer] 正在停止...")
        self.stop_event.set()
        self.join(timeout=5.0)
        
        print("[EventMaintainer] 已停止")
    
    def get_stats(self) -> dict:
        """
        获取统计信息
        
        Returns:
            dict: 统计信息
        """
        with self.events_lock:
            return {
                **self.stats,
                "current_events": len(self.events),
                "events_in_memory": len(self.events)
            }