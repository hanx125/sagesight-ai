"""
任务执行器（集成检测进程池）

展示如何将 DetectionProcessPool 集成到现有的 CameraWorker 中。
这是重构后的版本，使用检测进程池替代单线程检测器。

核心改进：
1. 检测任务提交到进程池（非阻塞）
2. 使用 mp.Event 等待检测结果
3. 解耦检测和跟踪（提高吞吐量）
"""

import sys
import os
import time
import threading
import multiprocessing as mp
from typing import Dict, Optional

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from src.detectors.detection_queue import DetectionTask
from src.detectors.detection_process_pool import DetectionProcessPool
from src.decoders.video_decoder import VideoDecoder
from src.utils.frame_index_queue import FrameIndexQueue


class CameraWorkerWithPool:
    """
    集成检测进程池的摄像机工作线程

    主要变化：
    1. 不再持有检测器实例（使用进程池）
    2. 通过 detection_queue 提交检测任务
    3. 使用 detection_event 等待检测结果
    """

    def __init__(
        self,
        camera_config: Dict,
        task_config: Dict,
        detection_pool: DetectionProcessPool,
        tracker_class
    ):
        """
        初始化摄像机工作线程

        Args:
            camera_config: 摄像机配置
            task_config: 任务配置
            detection_pool: 检测进程池（共享）
            tracker_class: 跟踪器类
        """
        self.camera_config = camera_config
        self.task_config = task_config
        self.detection_pool = detection_pool  # ⚠️ 共享的检测进程池
        self.tracker_class = tracker_class

        # 基础信息
        self.camera_id = camera_config.get('camera_id', 'unknown')
        self.rtsp_url = camera_config.get('rtsp_url', '')
        self.task_id = task_config.get('id')
        self.task_name = task_config.get('task_name', 'unknown')
        self.running = False

        # 配置
        self.decoder_cfg = task_config.get('decoder_config', {})
        self.detector_cfg = task_config.get('detector_config', {})
        self.tracker_cfg = task_config.get('tracker_config', {})

        # 组件（不包含检测器）
        self.decoder = None
        self.tracker = None
        self.frame_index_queue = None

        # 检测相关
        self.detection_event: Optional[mp.Event] = None  # 检测完成事件
        self.latest_detections = []  # 最新检测结果

        # 统计
        self.frame_count = 0
        self.detection_count = 0
        self.fps = 0.0

        print(f"[CameraWorker-{self.camera_id}] 初始化（使用检测进程池）")

    def init_components(self):
        """初始化组件"""
        try:
            # 1. 初始化解码器
            print(f"[CameraWorker-{self.camera_id}] 初始化解码器...")
            self.decoder = VideoDecoder(
                rtsp_url=self.rtsp_url,
                hwaccel_preset=self.decoder_cfg.get('hwaccel_preset', 'auto'),
                width=self.decoder_cfg.get('width', 640),
                height=self.decoder_cfg.get('height', 640),
                fps=self.decoder_cfg.get('fps', 5)
            )

            # 启动解码器
            if not self.decoder.start():
                raise RuntimeError("解码器启动失败")

            print(f"[CameraWorker-{self.camera_id}] 解码器启动成功")
            print(f"[CameraWorker-{self.camera_id}]   共享内存名称: {self.decoder.shm_name}")

            # 2. 初始化帧索引队列
            self.frame_index_queue = FrameIndexQueue(max_size=30)

            # 3. 注册到检测进程池
            self.detection_pool.register_camera(self.camera_id, self.decoder.shm_name)
            self.detection_event = self.detection_pool.get_detection_event(self.camera_id)

            print(f"[CameraWorker-{self.camera_id}] 已注册到检测进程池")

            # 4. 初始化跟踪器
            print(f"[CameraWorker-{self.camera_id}] 初始化跟踪器...")
            self.tracker = self.tracker_class(self.tracker_cfg)

            print(f"[CameraWorker-{self.camera_id}] 所有组件初始化成功")
            return True

        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] 组件初始化失败: {e}")
            return False

    def run(self):
        """主循环（线程入口）"""
        print(f"[CameraWorker-{self.camera_id}] 主循环启动")

        if not self.init_components():
            print(f"[CameraWorker-{self.camera_id}] 初始化失败，退出")
            return

        self.running = True
        last_fps_time = time.time()

        try:
            while self.running:
                # 1. 从解码器获取帧索引
                frame_index = self.decoder.get_frame_index(timeout=1.0)

                if frame_index is None:
                    continue

                # 2. 提交检测任务到进程池（非阻塞）
                detection_task = DetectionTask(
                    camera_id=self.camera_id,
                    frame_seq=frame_index.sequence_num,
                    shm_block_id=frame_index.shm_block_id,
                    shm_name=self.decoder.shm_name,
                    timestamp_ns=frame_index.timestamp_ns,
                    frame_shape=(self.decoder.height, self.decoder.width),
                    output_event_name=f"detection_done_{self.camera_id}",
                    detection_config=self.detector_cfg
                )

                success = self.detection_pool.submit_detection(detection_task)

                if not success:
                    print(f"[CameraWorker-{self.camera_id}] 警告：检测队列已满，跳过帧 {frame_index.sequence_num}")
                    continue

                # 3. 等待检测完成（阻塞，带超时）
                detection_done = self.detection_event.wait(timeout=5.0)

                if not detection_done:
                    print(f"[CameraWorker-{self.camera_id}] 警告：检测超时，跳过帧 {frame_index.sequence_num}")
                    self.detection_event.clear()
                    continue

                # 清除事件（准备下次检测）
                self.detection_event.clear()

                # 4. 获取检测结果（从共享变量或其他机制）
                # 注意：这里需要额外的机制来传递检测结果
                # 例如：使用 mp.Array 或共享字典
                detections = self.get_latest_detections()

                # 5. 跟踪
                tracked_objects = self.tracker.update(detections)

                # 6. 处理跟踪结果（生成事件、GIF等）
                self.handle_tracking_results(tracked_objects)

                # 统计
                self.frame_count += 1
                self.detection_count += len(detections)

                # 计算FPS
                current_time = time.time()
                if current_time - last_fps_time >= 1.0:
                    self.fps = self.frame_count / (current_time - last_fps_time)
                    print(f"[CameraWorker-{self.camera_id}] FPS: {self.fps:.1f}, 检测数: {self.detection_count}")
                    self.frame_count = 0
                    self.detection_count = 0
                    last_fps_time = current_time

        except KeyboardInterrupt:
            print(f"[CameraWorker-{self.camera_id}] 收到中断信号")
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] 主循环异常: {e}")
        finally:
            self.cleanup()

    def get_latest_detections(self) -> list:
        """
        获取最新检测结果

        注意：这需要额外的机制来传递检测结果
        例如：使用 mp.Array 或共享字典
        """
        # TODO: 实现检测结果传递机制
        # 可以使用 mp.Array 或 Manager.dict() 来存储检测结果
        return []

    def handle_tracking_results(self, tracked_objects):
        """处理跟踪结果"""
        # TODO: 实现事件生成、GIF生成等
        pass

    def cleanup(self):
        """清理资源"""
        print(f"[CameraWorker-{self.camera_id}] 清理资源...")

        self.running = False

        if self.decoder:
            self.decoder.stop()
            print(f"[CameraWorker-{self.camera_id}] 解码器已停止")

        print(f"[CameraWorker-{self.camera_id}] 资源清理完成")


# ========================================
# 使用示例
# ========================================

def example_usage():
    """使用检测进程池的示例"""

    # 1. 创建检测进程池
    detector_config = {
        'algorithm': 'yolov11',
        'model_path': 'models/yolov11.onnx',
        'confidence_threshold': 0.5
    }

    detection_pool = DetectionProcessPool(
        num_detectors=2,
        detector_config=detector_config,
        detection_queue_maxsize=100
    )

    # 2. 启动检测进程池
    detection_pool.start()
    print("检测进程池已启动")

    # 3. 创建摄像机工作线程
    camera_configs = [
        {
            'camera_id': 'cam-001',
            'rtsp_url': 'rtsp://admin:password@192.168.1.100:554/stream1'
        },
        {
            'camera_id': 'cam-002',
            'rtsp_url': 'rtsp://admin:password@192.168.1.101:554/stream1'
        }
    ]

    task_config = {
        'id': 1,
        'task_name': 'multi-camera-detection',
        'decoder_config': {'hwaccel_preset': 'auto', 'width': 640, 'height': 640},
        'detector_config': detector_config,
        'tracker_config': {}
    }

    # 4. 创建跟踪器类（示例）
    from src.tracking.botsort.botsort_tracker import BoTSORTTracker

    # 5. 创建摄像机工作线程
    workers = []
    for cam_config in camera_configs:
        worker = CameraWorkerWithPool(
            camera_config=cam_config,
            task_config=task_config,
            detection_pool=detection_pool,
            tracker_class=BoTSORTTracker
        )

        # 启动线程
        worker_thread = threading.Thread(target=worker.run, daemon=True)
        worker_thread.start()
        workers.append((worker, worker_thread))

    # 6. 运行一段时间
    try:
        time.sleep(60)
    except KeyboardInterrupt:
        print("\n收到中断信号，正在停止...")
    finally:
        # 7. 停止所有组件
        for worker, thread in workers:
            worker.running = False
            thread.join(timeout=5)

        detection_pool.stop()
        print("系统已停止")


if __name__ == '__main__':
    example_usage()