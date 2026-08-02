"""
远程检测器同步代理

RemoteObjectDetector 是 CameraWorker 侧的同步代理,封装了与检测器进程的通信:
1. CameraWorker 调用 remote_detector.detect(tensor_input)
2. 将 tensor_input 写入输入共享内存
3. 向 detection_queue 放入 camera_id（只传字符串,不传帧数据）
4. 等待 out_event 被检测器进程 set
5. 从输出共享内存读取检测结果
6. 按 threshold 过滤,返回 [(label, score, (x1,y1,x2,y2)), ...]

通信机制:
- 输入共享内存: (1, H, W, 3) uint8  [CameraWorker 写,检测器进程读]
- 输出共享内存: (20, 6) float32     [检测器进程写,CameraWorker 读]
- detection_queue: mp.Queue,放入 camera_id 字符串
- out_event: mp.Event,检测器进程完成后 set

这种设计避免了通过队列传递大块帧数据,实现零拷贝检测。
"""

import logging
import multiprocessing as mp
from typing import Optional

import numpy as np
from multiprocessing import shared_memory

from src.detectors.detector_process import (
    MAX_DETECTIONS,
    OUTPUT_FIELDS,
    get_input_shm_name,
    get_output_shm_name,
)

logger = logging.getLogger(__name__)


class RemoteObjectDetector:
    """
    远程检测器同步代理

    CameraWorker 通过此代理调用检测器进程进行检测。
    每个相机对应一个 RemoteObjectDetector 实例。

    使用示例:
        detector = RemoteObjectDetector(
            camera_id="cam001",
            model_type="yolov11",
            class_names=["person", "car"],
            detection_queue=pool.detection_queue,
            event=pool.out_events["cam001"],
            input_shape=(1, 640, 640, 3),
        )
        detections = detector.detect(tensor_input, threshold=0.4)
        # detections = [("person", 0.95, (100, 100, 200, 200)), ...]
    """

    def __init__(
        self,
        camera_id: str,
        model_type: str,
        class_names: list,
        detection_queue: mp.Queue,
        event: mp.Event,
        input_shape: tuple,
        stop_event: Optional[mp.Event] = None,
        task_id: str = "",
    ):
        """
        Args:
            camera_id: 摄像机ID（用作 detection_queue 的消息和共享内存命名）
            model_type: 模型类型（用于共享内存命名）
            class_names: 类别名称列表 ["person", "car", ...]
            detection_queue: 检测任务队列（放入 camera_id 字符串）
            event: 输出就绪事件（检测器进程完成后 set）
            input_shape: 输入共享内存形状 (1, H, W, 3)
            stop_event: 停止事件（可选,用于优雅退出）
            task_id: 任务ID（参与共享内存命名，确保同 camera 多任务各自独立 SHM）
        """
        self.camera_id = camera_id
        self.model_type = model_type
        self.class_names = class_names
        self.detection_queue = detection_queue
        self.event = event
        self.stop_event = stop_event
        self.task_id = task_id

        # 连接输入共享内存 (1, H, W, 3) uint8
        in_shm_name = get_input_shm_name(model_type, camera_id, task_id)
        self.in_shm = shared_memory.SharedMemory(name=in_shm_name, create=False)
        self.np_shm = np.ndarray(input_shape, dtype=np.uint8, buffer=self.in_shm.buf)

        # 连接输出共享内存 (20, 6) float32
        out_shm_name = get_output_shm_name(model_type, camera_id, task_id)
        self.out_shm = shared_memory.SharedMemory(name=out_shm_name, create=False)
        self.out_np_shm = np.ndarray(
            (MAX_DETECTIONS, OUTPUT_FIELDS), dtype=np.float32, buffer=self.out_shm.buf
        )

        logger.info(
            f"[RemoteObjectDetector-{camera_id}] 初始化完成: "
            f"model_type={model_type}, class_names数量={len(class_names)}, "
            f"前5个={class_names[:5] if class_names else '空'}"
        )

    def detect(
        self,
        tensor_input: np.ndarray,
        threshold: float = 0.4,
    ) -> list:
        """
        同步检测:写输入SHM → queue.put → wait event → 读输出SHM

        Args:
            tensor_input: (1, H, W, 3) uint8 输入张量
            threshold: 检测分数阈值,低于此值的检测被过滤

        Returns:
            检测结果列表 [(label, score, (x1, y1, x2, y2)), ...]
            超时或出错时返回空列表
        """
        # 检查停止事件
        if self.stop_event is not None and self.stop_event.is_set():
            return []

        # 写入输入共享内存
        self.np_shm[:] = tensor_input[:]

        # 清零输出共享内存（确保不会读到上一帧的残留数据）
        self.out_np_shm[:] = 0

        # 清除事件,放入检测任务
        self.event.clear()
        self.detection_queue.put(self.camera_id)

        # 等待检测完成（5秒超时）
        result = self.event.wait(timeout=5.0)

        if not result:
            logger.warning(
                f"[RemoteObjectDetector-{self.camera_id}] 检测超时(5s),跳过本帧"
            )
            return []

        # 从输出共享内存读取并过滤结果
        detections = []
        invalid_count = 0  # 连续无效结果计数
        for d in self.out_np_shm:
            # 输出格式: [class_id, score, x1, y1, x2, y2]
            score = float(d[1])
            if score < threshold:
                break

            class_id = int(d[0])

            # 获取类别名称（支持降级：class_names 为空时使用 class_N 格式）
            if self.class_names and 0 <= class_id < len(self.class_names):
                label = self.class_names[class_id]
                invalid_count = 0  # 重置计数
            elif not self.class_names:
                # class_names 为空，使用降级格式
                label = f"class_{class_id}"
                invalid_count = 0  # 重置计数
            else:
                invalid_count += 1
                # 连续3个以上无效class_id，视为共享内存残留数据，直接终止遍历
                if invalid_count >= 3:
                    logger.debug(
                        f"[RemoteObjectDetector-{self.camera_id}] "
                        f"连续{invalid_count}个无效class_id，终止遍历"
                    )
                    break
                logger.warning(
                    f"[RemoteObjectDetector-{self.camera_id}] "
                    f"无效的 class_id: {class_id} (共 {len(self.class_names)} 个类别),跳过"
                )
                continue

            box = (float(d[2]), float(d[3]), float(d[4]), float(d[5]))
            detections.append((label, score, box))

        return detections

    def cleanup(self):
        """关闭共享内存连接（不销毁,销毁由 DetectorPool 负责）"""
        try:
            self.in_shm.close()
        except Exception:
            pass
        try:
            self.out_shm.close()
        except Exception:
            pass
        logger.debug(f"[RemoteObjectDetector-{self.camera_id}] 共享内存已关闭")
