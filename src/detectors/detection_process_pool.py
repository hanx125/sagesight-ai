"""
检测进程池模块

管理多个独立的检测进程，提供检测任务分发和结果收集。
实现检测进程池化，充分利用 GPU 资源。

特性：
- 多进程并行检测（充分利用 GPU）
- 进程池管理（动态调整检测器数量）
- 事件通知机制（mp.Event）
- 自动重启（崩溃恢复）

使用场景：
- 多摄像机并行检测
- GPU 资源池化
- 高吞吐量检测系统
"""

import logging
import multiprocessing as mp
import signal
import sys
import time
from typing import Dict, Optional, List, Any
from pathlib import Path

from .detection_queue import DetectionQueue, DetectionTask, DetectionResult


logger = logging.getLogger(__name__)


def run_detector_process(
    detector_id: str,
    detection_queue: mp.Queue,
    detector_config: Dict[str, Any],
    output_events: Dict[str, mp.Event],
    shared_memory_names: Dict[str, str],
    detection_results: dict  # 新增：检测结果字典
):
    """
    检测进程主函数（模块级函数，用于 multiprocessing）

    Args:
        detector_id: 检测器ID（如 "detector-0"）
        detection_queue: 检测队列
        detector_config: 检测器配置
        output_events: 输出事件字典 {camera_id: mp.Event}
        shared_memory_names: 共享内存名称字典 {camera_id: shm_name}
        detection_results: 检测结果字典（进程间共享）
    """
    # 配置进程名称
    import setproctitle
    setproctitle.setproctitle(f"sagesight.detector.{detector_id}")

    # 配置信号处理
    stop_event = mp.Event()

    def signal_handler(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info(f"[{detector_id}] 检测进程启动 (PID: {mp.current_process().pid})")

    # 创建检测器实例
    try:
        from .factory import create_detector_from_config
        detector = create_detector_from_config(detector_config)
        detector.load_model()  # 加载模型
        logger.info(f"[{detector_id}] 检测器初始化成功: backend={detector.config.backend}")
    except Exception as e:
        logger.error(f"[{detector_id}] 检测器初始化失败: {e}", exc_info=True)
        return

    # 主循环：从队列获取任务并处理
    while not stop_event.is_set():
        try:
            # 从队列获取任务（超时1秒）
            task_data = detection_queue.get(timeout=1.0)

            if task_data is None:
                # 收到停止信号
                break

            # 解析任务
            task = DetectionTask(**task_data) if isinstance(task_data, dict) else task_data

            logger.debug(f"[{detector_id}] 处理任务: camera={task.camera_id} frame={task.frame_seq}")

            # 从共享内存读取帧（零拷贝）
            try:
                from ..utils.shared_memory import read_frame_from_shared_memory
                frame = read_frame_from_shared_memory(
                    shm_name=task.shm_name,
                    shm_block_id=task.shm_block_id,
                    frame_shape=task.frame_shape
                )

                if frame is None:
                    logger.warning(f"[{detector_id}] 无法从共享内存读取帧: {task.shm_name}:{task.shm_block_id}")
                    continue

                # 执行检测
                start_time = time.time()
                detections = detector.detect(frame)
                inference_time_ms = (time.time() - start_time) * 1000

                logger.debug(f"[{detector_id}] 检测完成: {len(detections)} 个目标, 耗时 {inference_time_ms:.1f}ms")

                # ========================================
                # 关键：将检测结果写入共享字典
                # ========================================
                # 注意：detection_results 是 mp.Manager().dict()，自动进程安全
                detection_results[task.camera_id] = {
                    'frame_seq': task.frame_seq,
                    'detections': detections,
                    'inference_time_ms': inference_time_ms,
                    'timestamp': time.time()
                }

                # 触发完成事件（通知摄像机进程）
                if task.camera_id in output_events:
                    output_events[task.camera_id].set()

                # 标记任务完成
                detection_queue.task_done()

            except Exception as e:
                logger.error(f"[{detector_id}] 处理任务失败: {e}", exc_info=True)

        except Exception as e:
            if "Empty" not in str(e):  # 忽略队列超时
                logger.error(f"[{detector_id}] 任务处理异常: {e}", exc_info=True)

    logger.info(f"[{detector_id}] 检测进程退出")


class DetectionProcessPool:
    """
    检测进程池管理器

    管理 N 个独立的检测进程，提供统一的任务分发接口。
    """

    def __init__(
        self,
        num_detectors: int = 2,
        detector_config: Dict[str, Any] = None,
        detection_queue_maxsize: int = 100
    ):
        """
        初始化检测进程池

        Args:
            num_detectors: 检测器数量（进程数），默认2
                          - 建议：根据 GPU 数量和显存大小调整
                          - 单 GPU：1-2 个进程
                          - 多 GPU：每 GPU 1-2 个进程
            detector_config: 检测器配置（算法、模型路径等）
            detection_queue_maxsize: 检测队列最大容量，默认100
        """
        self.num_detectors = num_detectors
        self.detector_config = detector_config or {}

        # 创建检测队列（进程级）
        self.detection_queue = DetectionQueue(maxsize=detection_queue_maxsize)

        # ========================================
        # 检测结果传递机制（关键新增）
        # ========================================
        # 使用 mp.Manager().dict() 实现进程间结果传递
        # 优势：
        # 1. 进程安全（自动同步）
        # 2. 支持复杂对象（列表、字典等）
        # 3. 简单易用（无需手动序列化）
        from multiprocessing import Manager
        self.manager = Manager()
        self.detection_results = self.manager.dict()  # {camera_id: detections_list}
        
        # 检测进程列表
        self.detector_processes: List[mp.Process] = []

        # 输出事件字典（用于通知检测完成）
        self.output_events: Dict[str, mp.Event] = {}

        # 共享内存名称字典（用于读取帧）
        self.shared_memory_names: Dict[str, str] = {}

        # 运行状态
        self.running = False

        logger.info(f"[DetectionProcessPool] 初始化完成")
        logger.info(f"  检测器数量: {num_detectors}")
        logger.info(f"  队列容量: {detection_queue_maxsize}")
        logger.info(f"  结果传递: mp.Manager().dict()")

    def start(self):
        """启动检测进程池"""
        if self.running:
            logger.warning("[DetectionProcessPool] 进程池已在运行")
            return

        logger.info("[DetectionProcessPool] 启动检测进程池...")

        # 启动检测进程
        for i in range(self.num_detectors):
            detector_id = f"detector-{i}"

            process = mp.Process(
                target=run_detector_process,
                args=(
                    detector_id,
                    self.detection_queue.queue,
                    self.detector_config,
                    self.output_events,
                    self.shared_memory_names,
                    self.detection_results  # 传递检测结果字典
                ),
                name=f"Detector-{i}",
                daemon=True
            )

            process.start()
            self.detector_processes.append(process)

            logger.info(f"[DetectionProcessPool] 启动检测进程: {detector_id} (PID: {process.pid})")

        self.running = True
        logger.info(f"[DetectionProcessPool] 检测进程池已启动，共 {self.num_detectors} 个进程")

    def stop(self):
        """停止检测进程池"""
        if not self.running:
            return

        logger.info("[DetectionProcessPool] 正在停止检测进程池...")

        # 发送停止信号（向队列放入 None）
        for _ in range(self.num_detectors):
            try:
                self.detection_queue.queue.put(None, timeout=1.0)
            except:
                pass

        # 等待进程退出（最多5秒）
        for i, process in enumerate(self.detector_processes):
            process.join(timeout=5)

            if process.is_alive():
                logger.warning(f"[DetectionProcessPool] 强制终止检测进程-{i}")
                process.terminate()
                process.join()

        self.detector_processes.clear()
        self.running = False

        logger.info("[DetectionProcessPool] 检测进程池已停止")

    def submit_detection(self, task: DetectionTask) -> bool:
        """
        提交检测任务（非阻塞）

        Args:
            task: 检测任务

        Returns:
            bool: 是否成功提交
        """
        if not self.running:
            logger.warning("[DetectionProcessPool] 进程池未运行，无法提交任务")
            return False

        # 注册输出事件（如果尚未注册）
        if task.camera_id not in self.output_events:
            self.output_events[task.camera_id] = mp.Event()
            logger.info(f"[DetectionProcessPool] 注册输出事件: camera_id={task.camera_id}")

        # 注册共享内存名称（如果尚未注册）
        if task.camera_id not in self.shared_memory_names:
            self.shared_memory_names[task.camera_id] = task.shm_name

        # 提交任务到队列
        success = self.detection_queue.submit_task(task)

        if not success:
            logger.warning(f"[DetectionProcessPool] 队列已满，丢弃任务: camera={task.camera_id} frame={task.frame_seq}")

        return success

    def register_camera(self, camera_id: str, shm_name: str):
        """
        注册摄像机（用于共享内存管理）

        Args:
            camera_id: 摄像机ID
            shm_name: 共享内存名称
        """
        if camera_id not in self.output_events:
            self.output_events[camera_id] = mp.Event()
            logger.info(f"[DetectionProcessPool] 注册摄像机: {camera_id}")

        self.shared_memory_names[camera_id] = shm_name

    def get_detection_event(self, camera_id: str) -> Optional[mp.Event]:
        """
        获取检测完成事件（用于等待检测结果）

        Args:
            camera_id: 摄像机ID

        Returns:
            mp.Event: 检测完成事件，如果未注册则返回None
        """
        return self.output_events.get(camera_id)

    def wait_for_detection(self, camera_id: str, timeout: float = 5.0) -> bool:
        """
        等待检测完成（阻塞）

        Args:
            camera_id: 摄像机ID
            timeout: 超时时间（秒）

        Returns:
            bool: 是否成功完成
        """
        event = self.get_detection_event(camera_id)
        if event is None:
            logger.warning(f"[DetectionProcessPool] 未找到摄像机事件: {camera_id}")
            return False

        # 等待事件触发
        success = event.wait(timeout=timeout)

        if success:
            # 清除事件（准备下次检测）
            event.clear()

        return success

    def get_detection_results(self, camera_id: str) -> List[Dict]:
        """
        获取检测结果（关键方法）

        Args:
            camera_id: 摄像机ID

        Returns:
            list: 检测结果列表，如果无结果则返回空列表
        """
        result_data = self.detection_results.get(camera_id)
        
        if result_data is None:
            return []
        
        # 返回检测结果列表
        return result_data.get('detections', [])

    def get_detection_stats(self, camera_id: str) -> Optional[Dict]:
        """
        获取检测统计信息

        Args:
            camera_id: 摄像机ID

        Returns:
            dict: 统计信息（帧序列、推理时间等），如果无结果则返回None
        """
        result_data = self.detection_results.get(camera_id)
        
        if result_data is None:
            return None
        
        return {
            'frame_seq': result_data.get('frame_seq'),
            'inference_time_ms': result_data.get('inference_time_ms'),
            'timestamp': result_data.get('timestamp'),
            'detection_count': len(result_data.get('detections', []))
        }

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        queue_stats = self.detection_queue.get_stats()

        return {
            'num_detectors': self.num_detectors,
            'running': self.running,
            'active_processes': sum(1 for p in self.detector_processes if p.is_alive()),
            'registered_cameras': len(self.output_events),
            **queue_stats
        }

    def print_stats(self):
        """打印统计信息"""
        stats = self.get_stats()
        logger.info("[DetectionProcessPool] 统计信息:")
        logger.info(f"  检测器数量: {stats['num_detectors']}")
        logger.info(f"  运行状态: {stats['running']}")
        logger.info(f"  活跃进程: {stats['active_processes']}")
        logger.info(f"  注册摄像机: {stats['registered_cameras']}")
        self.detection_queue.print_stats()

    def __del__(self):
        """析构函数（自动停止进程池）"""
        if self.running:
            self.stop()