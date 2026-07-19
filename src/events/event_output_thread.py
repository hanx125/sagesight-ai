"""
事件输出线程

提供独立的事件输出线程，避免阻塞检测流程：
- 使用队列接收事件数据（生产者-消费者模式）
- 异步处理事件输出
- 支持缩略图管理和属性事件

使用场景：
- 多线程环境（避免阻塞检测）
- 高并发事件输出
- 事件生命周期管理

参考：
- Frigate EventMaintainer
"""

import threading
import queue
import time
from typing import Dict, Optional, Any
from dataclasses import dataclass
import numpy as np


@dataclass
class EventTask:
    """事件任务"""
    task_type: str  # 任务类型：emit, update_thumbnail, clear_thumbnail, emit_attribute
    data: Dict  # 任务数据
    
    # 可选字段
    image: Optional[np.ndarray] = None
    frame_id: int = 0
    task_id: Optional[int] = None
    algorithm: Optional[str] = None
    metadata: Optional[Dict] = None


class EventOutputThread(threading.Thread):
    """
    事件输出线程
    
    特性：
    - 独立线程处理事件输出（不阻塞检测）
    - 队列缓冲（支持高并发）
    - 异步缩略图管理
    - 属性事件支持
    """
    
    def __init__(self, event_output, max_queue_size: int = 100):
        """
        初始化事件输出线程
        
        Args:
            event_output: EventOutput 实例
            max_queue_size: 队列最大容量
        """
        super().__init__(name="EventOutputThread", daemon=True)
        
        self.event_output = event_output
        self.max_queue_size = max_queue_size
        
        # 事件队列
        self.event_queue = queue.Queue(maxsize=max_queue_size)
        
        # 停止事件
        self.stop_event = threading.Event()
        
        # 统计信息
        self.stats = {
            'total_events': 0,
            'processed_events': 0,
            'failed_events': 0,
            'queue_full_count': 0
        }
        
        print(f"[EventOutputThread] 初始化完成")
        print(f"[EventOutputThread]   队列容量: {max_queue_size}")
    
    def submit_event(self, event_type: str, data: Dict,
                    image: Optional[np.ndarray] = None,
                    frame_id: int = 0, task_id: Optional[int] = None,
                    algorithm: Optional[str] = None,
                    metadata: Optional[Dict] = None) -> bool:
        """
        提交事件到队列（非阻塞）

        Args:
            event_type: 事件类型
            data: 事件数据
            image: 图片数据
            frame_id: 帧 ID
            task_id: 任务 ID
            algorithm: 算法名称
            metadata: 元数据

        Returns:
            bool: 是否成功提交
        """
        try:
            # 将 event_type 加入到 data 中，便于后续处理
            data_with_type = {**data, 'event_type': event_type}

            task = EventTask(
                task_type='emit',
                data=data_with_type,
                image=image,
                frame_id=frame_id,
                task_id=task_id,
                algorithm=algorithm,
                metadata=metadata
            )

            # 非阻塞提交（如果队列满，立即返回）
            self.event_queue.put_nowait(task)
            self.stats['total_events'] += 1

            return True

        except queue.Full:
            # 队列已满，丢弃事件
            self.stats['queue_full_count'] += 1
            print(f"[EventOutputThread] 队列已满，丢弃事件: {event_type}")
            return False
    
    def submit_thumbnail_update(self, event_id: str, image: np.ndarray,
                                score: float, bbox: tuple) -> bool:
        """
        提交缩略图更新任务（非阻塞）
        
        Args:
            event_id: 事件 ID
            image: 图片数据
            score: 检测分数
            bbox: 边界框
            
        Returns:
            bool: 是否成功提交
        """
        try:
            task = EventTask(
                task_type='update_thumbnail',
                data={
                    'event_id': event_id,
                    'score': score,
                    'bbox': bbox
                },
                image=image
            )
            
            self.event_queue.put_nowait(task)
            return True
            
        except queue.Full:
            print(f"[EventOutputThread] 队列已满，丢弃缩略图更新: {event_id}")
            return False
    
    def submit_thumbnail_clear(self, event_id: str) -> bool:
        """
        提交缩略图清理任务（非阻塞）
        
        Args:
            event_id: 事件 ID
            
        Returns:
            bool: 是否成功提交
        """
        try:
            task = EventTask(
                task_type='clear_thumbnail',
                data={'event_id': event_id}
            )
            
            self.event_queue.put_nowait(task)
            return True
            
        except queue.Full:
            print(f"[EventOutputThread] 队列已满，丢弃缩略图清理: {event_id}")
            return False
    
    def submit_attribute_event(self, track_id: int, attribute_type: str,
                              attribute_value: str, confidence: float,
                              bbox: tuple, image: Optional[np.ndarray] = None) -> bool:
        """
        提交属性事件（非阻塞）
        
        Args:
            track_id: 跟踪 ID
            attribute_type: 属性类型
            attribute_value: 属性值
            confidence: 置信度
            bbox: 边界框
            image: 图片数据
            
        Returns:
            bool: 是否成功提交
        """
        try:
            task = EventTask(
                task_type='emit_attribute',
                data={
                    'track_id': track_id,
                    'attribute_type': attribute_type,
                    'attribute_value': attribute_value,
                    'confidence': confidence,
                    'bbox': bbox
                },
                image=image
            )
            
            self.event_queue.put_nowait(task)
            self.stats['total_events'] += 1
            return True
            
        except queue.Full:
            print(f"[EventOutputThread] 队列已满，丢弃属性事件: {attribute_type}")
            return False
    
    def run(self):
        """事件处理主循环"""
        print(f"[EventOutputThread] 线程启动")
        
        while not self.stop_event.is_set():
            try:
                # 从队列获取任务（超时 1 秒）
                task = self.event_queue.get(timeout=1.0)
                
                # 处理任务
                self._process_task(task)
                
                # 更新统计
                self.stats['processed_events'] += 1
                
            except queue.Empty:
                # 队列为空，继续等待
                continue
                
            except Exception as e:
                # 处理异常
                self.stats['failed_events'] += 1
                print(f"[EventOutputThread] 任务处理异常: {e}")
                import traceback
                traceback.print_exc()
        
        # 处理剩余任务
        self._drain_queue()
        
        print(f"[EventOutputThread] 线程停止")
        print(f"[EventOutputThread]   处理事件: {self.stats['processed_events']}")
        print(f"[EventOutputThread]   失败事件: {self.stats['failed_events']}")
        print(f"[EventOutputThread]   队列满次数: {self.stats['queue_full_count']}")
    
    def _process_task(self, task: EventTask):
        """
        处理事件任务
        
        Args:
            task: 事件任务
        """
        if task.task_type == 'emit':
            # 发送事件
            self.event_output.emit(
                event_type=task.data.get('event_type', 'unknown'),
                data=task.data,
                image=task.image,
                frame_id=task.frame_id,
                task_id=task.task_id,
                algorithm=task.algorithm,
                metadata=task.metadata
            )
            
        elif task.task_type == 'update_thumbnail':
            # 更新缩略图
            self.event_output.update_thumbnail(
                event_id=task.data['event_id'],
                image=task.image,
                score=task.data['score'],
                bbox=task.data['bbox']
            )
            
        elif task.task_type == 'clear_thumbnail':
            # 清除缩略图
            self.event_output.clear_thumbnail(
                event_id=task.data['event_id']
            )
            
        elif task.task_type == 'emit_attribute':
            # 发送属性事件
            self.event_output.emit_attribute_event(
                track_id=task.data['track_id'],
                attribute_type=task.data['attribute_type'],
                attribute_value=task.data['attribute_value'],
                confidence=task.data['confidence'],
                bbox=task.data['bbox'],
                image=task.image
            )
    
    def _drain_queue(self):
        """处理队列中的剩余任务"""
        print(f"[EventOutputThread] 处理剩余任务...")
        
        remaining_count = 0
        while not self.event_queue.empty():
            try:
                task = self.event_queue.get_nowait()
                self._process_task(task)
                remaining_count += 1
            except queue.Empty:
                break
            except Exception as e:
                print(f"[EventOutputThread] 剩余任务处理失败: {e}")
        
        if remaining_count > 0:
            print(f"[EventOutputThread] 处理了 {remaining_count} 个剩余任务")
    
    def stop(self):
        """停止线程"""
        print(f"[EventOutputThread] 正在停止...")
        self.stop_event.set()
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            'queue_size': self.event_queue.qsize(),
            'max_queue_size': self.max_queue_size
        }