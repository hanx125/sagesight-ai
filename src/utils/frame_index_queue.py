"""
帧索引队列模块

实现索引队列，不存储完整帧数据，只存储SharedMemory块的索引。
负责数据生产和消费的调度与同步（解耦）。

特性：
- 索引存储（不存储完整数据）
- 线程安全的FIFO队列
- 生产者-消费者模式
- 低内存占用

使用场景：
VideoDecoder生产帧索引 → FrameIndexQueue → CameraWorker消费帧索引
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Deque


@dataclass
class FrameIndex:
    """
    帧索引（不存储数据）
    
    只存储SharedMemory块的索引信息，不存储完整的帧数据。
    用于调度和同步，实现生产者-消费者解耦。
    """
    sequence_num: int      # 帧序列号
    shm_block_id: int      # SharedMemory块ID
    timestamp_ns: int      # 时间戳（纳秒）
    enqueue_time_ns: int = field(default_factory=lambda: time.time_ns())
    
    @property
    def queue_delay_ns(self) -> int:
        """在队列中的延迟（纳秒）"""
        return time.time_ns() - self.enqueue_time_ns
    
    @property
    def queue_delay_ms(self) -> float:
        """在队列中的延迟（毫秒）"""
        return self.queue_delay_ns / 1_000_000


@dataclass
class FrameIndexQueueStats:
    """索引队列统计信息"""
    total_indices: int = 0           # 总入队索引数
    current_size: int = 0           # 当前队列大小
    max_size: int = 0               # 最大队列大小
    avg_queue_delay_ms: float = 0.0  # 平均队列延迟


class FrameIndexQueue:
    """
    帧索引队列
    
    特性：
    - 线程安全的FIFO队列
    - 索引存储（不存储数据）
    - 生产者-消费者模式
    - 低内存占用
    """
    
    def __init__(self, max_size: int = 30):
        """
        初始化索引队列
        
        Args:
            max_size: 队列最大容量（索引数），默认30
                     - 太小：容易丢帧
                     - 太大：增加延迟
                     推荐值：10-50（根据帧率和处理速度调整）
        """
        self.max_size = max_size
        self._queue: Deque[FrameIndex] = deque(maxlen=max_size)
        self._lock = threading.Lock()
        
        # 统计信息
        self.stats = FrameIndexQueueStats(max_size=max_size)
        
        print(f"[FrameIndexQueue] 初始化完成")
        print(f"[FrameIndexQueue]   最大容量: {max_size} 个索引")
    
    def enqueue_index(self, sequence_num: int, shm_block_id: int, timestamp_ns: int) -> bool:
        """
        加入帧索引
        
        Args:
            sequence_num: 帧序列号
            shm_block_id: SharedMemory块ID
            timestamp_ns: 时间戳
        
        Returns:
            bool: 是否成功加入
        """
        with self._lock:
            # 创建帧索引（不存储数据）
            frame_index = FrameIndex(
                sequence_num=sequence_num,
                shm_block_id=shm_block_id,
                timestamp_ns=timestamp_ns
            )
            
            # 加入队列
            self._queue.append(frame_index)
            
            # 更新统计
            self.stats.total_indices += 1
            self.stats.current_size = len(self._queue)
            
            return True
    
    def dequeue_index(self) -> Optional[FrameIndex]:
        """
        取出帧索引
        
        Returns:
            FrameIndex: 帧索引，如果队列为空则返回None
        """
        with self._lock:
            if not self._queue:
                return None
            
            # 取出索引
            frame_index = self._queue.popleft()
            
            # 更新统计
            self.stats.current_size = len(self._queue)
            
            return frame_index
    
    def peek_index(self) -> Optional[FrameIndex]:
        """
        查看队首索引（不取出）
        
        Returns:
            FrameIndex: 帧索引，如果队列为空则返回None
        """
        with self._lock:
            if not self._queue:
                return None
            return self._queue[0]
    
    def size(self) -> int:
        """获取队列大小"""
        with self._lock:
            return len(self._queue)
    
    def is_empty(self) -> bool:
        """队列是否为空"""
        with self._lock:
            return len(self._queue) == 0
    
    def is_full(self) -> bool:
        """队列是否已满"""
        with self._lock:
            return len(self._queue) >= self.max_size
    
    def clear(self):
        """清空队列"""
        with self._lock:
            self._queue.clear()
            self.stats.current_size = 0
    
    def get_stats(self) -> FrameIndexQueueStats:
        """获取统计信息"""
        with self._lock:
            return self.stats
    
    def print_stats(self):
        """打印统计信息"""
        stats = self.get_stats()
        print(f"[FrameIndexQueue] 统计信息:")
        print(f"  总索引数: {stats.total_indices}")
        print(f"  当前大小: {stats.current_size}")
        print(f"  最大容量: {stats.max_size}")