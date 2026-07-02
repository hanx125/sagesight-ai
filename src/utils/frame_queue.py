"""
帧队列管理模块

功能：
- 缓存多帧 YUV 数据，防止检测器处理速度跟不上时丢帧
- 线程安全的 FIFO 队列管理
- 性能监控（队列长度、丢帧数、延迟统计）

使用场景：
VideoDecoder 解码后的帧先写入 FrameQueue，再写入共享内存
YOLODetector 从 FrameQueue 读取帧进行处理
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Deque
import numpy as np

from .shared_memory import FrameHeader


@dataclass
class QueuedFrame:
    """队列中的帧数据"""
    header: FrameHeader
    y_plane: np.ndarray
    u_plane: np.ndarray
    v_plane: np.ndarray
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
class FrameQueueStats:
    """帧队列统计信息"""
    total_frames: int = 0           # 总入队帧数
    dropped_frames: int = 0         # 丢弃的帧数（队列满时）
    current_size: int = 0           # 当前队列大小
    max_size: int = 0               # 最大队列大小
    avg_queue_delay_ms: float = 0.0  # 平均队列延迟
    min_queue_delay_ms: float = 0.0  # 最小队列延迟
    max_queue_delay_ms: float = 0.0  # 最大队列延迟


class FrameQueue:
    """
    帧队列管理器
    
    特性：
    - 线程安全的 FIFO 队列
    - 可配置的最大容量
    - 自动丢弃最旧的帧（当队列满时）
    - 性能统计监控
    """
    
    def __init__(self, max_size: int = 5):
        """
        初始化帧队列
        
        Args:
            max_size: 队列最大容量（帧数），默认 5 帧
                     - 太小：容易丢帧
                     - 太大：增加延迟
                     推荐值：3-10 帧
        """
        self.max_size = max_size
        self._queue: Deque[QueuedFrame] = deque(maxlen=max_size)
        self._lock = threading.Lock()
        
        # 统计信息
        self._stats = FrameQueueStats(max_size=max_size)
        self._delay_history: List[float] = []  # 延迟历史记录（用于统计）
        
        # 性能指标
        self._enqueue_count = 0
        self._dequeue_count = 0
        self._drop_count = 0
    
    def enqueue(self, header: FrameHeader, y: np.ndarray, u: np.ndarray, v: np.ndarray) -> bool:
        """
        将帧加入队列
        
        Args:
            header: 帧头部信息
            y: Y 平面 (640, 640)
            u: U 平面 (320, 320)
            v: V 平面 (320, 320)
        
        Returns:
            bool: 是否成功入队（队列满时返回 False）
        """
        with self._lock:
            # 检查队列是否已满
            is_full = len(self._queue) >= self.max_size
            
            if is_full:
                # 队列满，丢弃最旧的帧
                dropped_frame = self._queue[0] if len(self._queue) > 0 else None
                self._drop_count += 1
                self._stats.dropped_frames += 1
            
            # 创建队列帧
            q_frame = QueuedFrame(
                header=header,
                y_plane=y.copy(),  # 深拷贝，避免数据被修改
                u_plane=u.copy(),
                v_plane=v.copy(),
                enqueue_time_ns=time.time_ns()
            )
            
            # 添加到队列
            self._queue.append(q_frame)
            self._enqueue_count += 1
            self._stats.total_frames += 1
            self._stats.current_size = len(self._queue)
            
            # 更新最大队列大小
            if len(self._queue) > self._stats.max_size:
                self._stats.max_size = len(self._queue)
            
            return not is_full
    
    def dequeue(self) -> Optional[QueuedFrame]:
        """
        从队列取出最旧的帧
        
        Returns:
            QueuedFrame: 队列中最旧的帧，如果队列为空则返回 None
        """
        with self._lock:
            if len(self._queue) == 0:
                return None
            
            # 取出最旧的帧
            q_frame = self._queue.popleft()
            self._dequeue_count += 1
            self._stats.current_size = len(self._queue)
            
            # 记录延迟统计
            delay_ms = q_frame.queue_delay_ms
            self._delay_history.append(delay_ms)
            
            # 限制历史记录长度
            if len(self._delay_history) > 100:
                self._delay_history = self._delay_history[-100:]
            
            # 更新延迟统计
            self._update_delay_stats()
            
            return q_frame
    
    def dequeue_latest(self) -> Optional[QueuedFrame]:
        """
        从队列取出最新的帧（丢弃中间帧）
        
        使用场景：检测器处理速度跟不上时，直接获取最新帧，跳过中间帧
        
        Returns:
            QueuedFrame: 队列中最新的帧，如果队列为空则返回 None
        """
        with self._lock:
            if len(self._queue) == 0:
                return None
            
            # 取出最新的帧（队尾）
            q_frame = self._queue[-1]
            self._queue.clear()  # 清空队列
            self._dequeue_count += 1
            self._stats.current_size = 0
            
            # 记录延迟统计
            delay_ms = q_frame.queue_delay_ms
            self._delay_history.append(delay_ms)
            
            if len(self._delay_history) > 100:
                self._delay_history = self._delay_history[-100:]
            
            self._update_delay_stats()
            
            return q_frame
    
    def peek(self) -> Optional[QueuedFrame]:
        """
        查看队列最旧的帧（不取出）
        
        Returns:
            QueuedFrame: 队列中最旧的帧，如果队列为空则返回 None
        """
        with self._lock:
            if len(self._queue) == 0:
                return None
            return self._queue[0]
    
    def size(self) -> int:
        """获取当前队列大小"""
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
            self._stats.current_size = 0
    
    def get_stats(self) -> FrameQueueStats:
        """获取统计信息"""
        with self._lock:
            stats = FrameQueueStats(
                total_frames=self._stats.total_frames,
                dropped_frames=self._stats.dropped_frames,
                current_size=self._stats.current_size,
                max_size=self._stats.max_size,
                avg_queue_delay_ms=self._stats.avg_queue_delay_ms,
                min_queue_delay_ms=self._stats.min_queue_delay_ms,
                max_queue_delay_ms=self._stats.max_queue_delay_ms
            )
            return stats
    
    def _update_delay_stats(self):
        """更新延迟统计信息（需要在锁内调用）"""
        if len(self._delay_history) == 0:
            return
        
        self._stats.avg_queue_delay_ms = sum(self._delay_history) / len(self._delay_history)
        self._stats.min_queue_delay_ms = min(self._delay_history)
        self._stats.max_queue_delay_ms = max(self._delay_history)
    
    def print_stats(self):
        """打印统计信息"""
        stats = self.get_stats()
        print("\n" + "="*60)
        print("帧队列统计信息")
        print("="*60)
        print(f"队列容量：{stats.max_size} 帧")
        print(f"当前大小：{stats.current_size} 帧")
        print(f"总入队帧数：{stats.total_frames}")
        print(f"总出队帧数：{self._dequeue_count}")
        print(f"丢弃帧数：{stats.dropped_frames} ({stats.dropped_frames / max(stats.total_frames, 1) * 100:.2f}%)")
        print(f"队列延迟:")
        print(f"  平均：{stats.avg_queue_delay_ms:.2f} ms")
        print(f"  最小：{stats.min_queue_delay_ms:.2f} ms")
        print(f"  最大：{stats.max_queue_delay_ms:.2f} ms")
        print("="*60)


class FrameQueueMonitor:
    """
    帧队列性能监控器
    
    功能：
    - 实时监控队列状态
    - 检测队列异常（如队列持续满、持续空）
    - 性能告警
    """
    
    def __init__(self, frame_queue: FrameQueue, check_interval_ms: float = 1000):
        """
        初始化监控器
        
        Args:
            frame_queue: 要监控的帧队列
            check_interval_ms: 检查间隔（毫秒）
        """
        self.frame_queue = frame_queue
        self.check_interval_ms = check_interval_ms
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        
        # 告警阈值
        self.full_threshold = 0.9      # 队列持续满的阈值（90%）
        self.empty_threshold = 0.1     # 队列持续空的阈值（10%）
        self.delay_threshold_ms = 100  # 延迟告警阈值（毫秒）
        
        # 连续异常计数
        self._consecutive_full = 0
        self._consecutive_empty = 0
    
    def start(self):
        """启动监控线程"""
        if self._running:
            return
        
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        print(f"[FrameQueueMonitor] 监控已启动（间隔：{self.check_interval_ms}ms）")
    
    def stop(self):
        """停止监控线程"""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)
            self._monitor_thread = None
        print(f"[FrameQueueMonitor] 监控已停止")
    
    def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                stats = self.frame_queue.get_stats()
                current_size = stats.current_size
                max_size = stats.max_size
                
                # 检查队列是否持续满
                if current_size >= max_size * self.full_threshold:
                    self._consecutive_full += 1
                    if self._consecutive_full >= 3:
                        print(f"[WARN] 帧队列持续满（{current_size}/{max_size}），检测器处理速度可能跟不上")
                else:
                    self._consecutive_full = 0
                
                # 检查队列是否持续空
                if current_size <= max_size * self.empty_threshold:
                    self._consecutive_empty += 1
                    if self._consecutive_empty >= 3:
                        print(f"[WARN] 帧队列持续空（{current_size}/{max_size}），解码器速度可能不足")
                else:
                    self._consecutive_empty = 0
                
                # 检查延迟
                if stats.avg_queue_delay_ms > self.delay_threshold_ms:
                    print(f"[WARN] 队列延迟过高：{stats.avg_queue_delay_ms:.2f}ms (阈值：{self.delay_threshold_ms}ms)")
                
                # 休眠
                time.sleep(self.check_interval_ms / 1000)
                
            except Exception as e:
                print(f"[FrameQueueMonitor] 监控错误：{e}")
                time.sleep(1)
