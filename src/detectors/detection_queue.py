"""
检测队列模块（进程级）

实现检测任务的进程间队列，用于摄像机进程和检测进程池之间通信。
使用 multiprocessing.Queue 实现，支持跨进程传递检测任务。

特性：
- 进程级队列（支持跨进程）
- 检测任务封装（camera_id + 帧索引 + 共享内存信息）
- 非阻塞提交（避免阻塞摄像机进程）
- 超时获取（避免检测进程空转）

使用场景：
摄像机进程 → DetectionQueue → 检测进程池
"""

import multiprocessing as mp
import queue
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class DetectionTask:
    """
    检测任务（进程间传递）

    包含检测所需的所有信息，不包含实际的帧数据。
    检测进程从共享内存读取帧数据（零拷贝）。
    """
    camera_id: str              # 摄像机ID
    frame_seq: int               # 帧序列号
    shm_block_id: int            # 共享内存块ID
    shm_name: str                # 共享内存名称
    timestamp_ns: int            # 时间戳（纳秒）
    frame_shape: tuple           # 帧形状（height, width）
    output_event_name: str       # 输出事件名称（用于通知完成）
    task_id: str = ""            # 任务ID（用于全局检测池模式）
    detection_config: Dict[str, Any] = None  # 检测配置（可选，用于动态调整）


@dataclass
class DetectionResult:
    """
    检测结果（进程间传递）

    包含检测结果和状态信息。
    """
    camera_id: str               # 摄像机ID
    frame_seq: int               # 帧序列号
    detections: list             # 检测结果列表 [(class_name, confidence, bbox), ...]
    inference_time_ms: float     # 推理时间（毫秒）
    success: bool                # 是否成功
    error_message: str = ''      # 错误信息（如果失败）


class DetectionQueue:
    """
    检测队列（进程级）

    特性：
    - 进程安全（使用 mp.Queue）
    - 非阻塞提交
    - 超时获取
    - 支持统计信息
    """

    def __init__(self, maxsize: int = 100):
        """
        初始化检测队列

        Args:
            maxsize: 队列最大容量，默认100
                     - 太小：容易丢失检测任务
                     - 太大：增加内存占用和延迟
                     推荐值：50-200（根据摄像机数量和检测速度调整）
        """
        self.maxsize = maxsize
        self.queue = mp.Queue(maxsize=maxsize)

        # 统计信息（使用 mp.Value 实现进程间共享）
        self.stats_total_submitted = mp.Value('i', 0)
        self.stats_total_processed = mp.Value('i', 0)
        self.stats_current_size = mp.Value('i', 0)

        print(f"[DetectionQueue] 初始化完成")
        print(f"[DetectionQueue]   最大容量: {maxsize} 个任务")
        print(f"[DetectionQueue]   进程级队列: mp.Queue")

    def submit_task(self, task: DetectionTask) -> bool:
        """
        提交检测任务（非阻塞）

        Args:
            task: 检测任务

        Returns:
            bool: 是否成功提交
                  - True: 成功提交
                  - False: 队列已满，任务被丢弃
        """
        try:
            self.queue.put_nowait(task)

            # 更新统计
            with self.stats_total_submitted.get_lock():
                self.stats_total_submitted.value += 1
            with self.stats_current_size.get_lock():
                self.stats_current_size.value += 1

            # ✅ 新增：打印提交成功日志
            print(f"[DetectionQueue] 任务提交成功: camera={task.camera_id} frame={task.frame_seq}")

            return True

        except queue.Full:
            # 队列已满，丢弃任务（避免阻塞）
            print(f"[DetectionQueue] 警告：队列已满，丢弃任务 camera_id={task.camera_id} frame_seq={task.frame_seq}")
            return False

    def get_task(self, timeout: float = 1.0) -> Optional[DetectionTask]:
        """
        获取检测任务（阻塞）

        Args:
            timeout: 超时时间（秒），默认1.0

        Returns:
            DetectionTask: 检测任务，如果超时则返回None
        """
        try:
            task = self.queue.get(timeout=timeout)

            # 更新统计
            with self.stats_current_size.get_lock():
                self.stats_current_size.value -= 1

            return task

        except queue.Empty:
            # 队列为空，超时返回
            return None

    def task_done(self):
        """
        标记任务完成（用于统计）

        在检测进程处理完任务后调用。
        """
        with self.stats_total_processed.get_lock():
            self.stats_total_processed.value += 1

    def size(self) -> int:
        """获取队列当前大小"""
        with self.stats_current_size.get_lock():
            return self.stats_current_size.value

    def is_empty(self) -> bool:
        """队列是否为空"""
        return self.size() == 0

    def is_full(self) -> bool:
        """队列是否已满"""
        return self.size() >= self.maxsize

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        with self.stats_total_submitted.get_lock(), \
             self.stats_total_processed.get_lock(), \
             self.stats_current_size.get_lock():
            return {
                'total_submitted': self.stats_total_submitted.value,
                'total_processed': self.stats_total_processed.value,
                'current_size': self.stats_current_size.value,
                'max_size': self.maxsize,
                'pending_tasks': self.stats_total_submitted.value - self.stats_total_processed.value
            }

    def print_stats(self):
        """打印统计信息"""
        stats = self.get_stats()
        print(f"[DetectionQueue] 统计信息:")
        print(f"  已提交任务: {stats['total_submitted']}")
        print(f"  已处理任务: {stats['total_processed']}")
        print(f"  当前队列大小: {stats['current_size']}")
        print(f"  最大容量: {stats['max_size']}")
        print(f"  待处理任务: {stats['pending_tasks']}")