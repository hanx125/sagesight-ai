"""
检测器进程池模块

实现多模型检测器进程池管理,支持:
- 模式A(1算法多相机): 所有任务用同一 model_type,共享一个 DetectorPool
- 模式B(多算法并行): 不同任务用不同 model_type,各有独立 DetectorPool 和独立检测器进程

架构:
  DetectorPoolManager
    ├── pools: dict[model_type, DetectorPool]
    │   ├── "yolov11" → DetectorPool（N个检测器进程,共享 detection_queue）
    │   └── "yolov8"  → DetectorPool（N个检测器进程）
    │
    └── register_task(task_id, camera_ids, model_type, ...) → {camera_id: RemoteObjectDetector}

通信机制:
  - CameraWorker → 检测器进程: detection_queue.put(camera_id) [字符串]
  - 检测器进程 → CameraWorker: out_events[camera_id].set() + 输出SHM
  - 输入SHM: (1, H, W, 3) uint8  [DetectorPool创建,CameraWorker写,检测器读]
  - 输出SHM: (20, 6) float32     [DetectorPool创建,检测器写,CameraWorker读]

生命周期:
  1. register_task() 注册任务,创建共享内存和 RemoteObjectDetector
  2. start_all() 创建检测器进程（必须所有相机注册完成后调用）
  3. CameraWorker 通过 RemoteObjectDetector.detect() 同步检测
  4. stop() 停止所有进程,清理共享内存
"""

import logging
import multiprocessing as mp
from typing import Optional

import numpy as np
from multiprocessing import shared_memory

from src.detectors.detector_process import (
    MAX_DETECTIONS,
    OUTPUT_FIELDS,
    ObjectDetectProcess,
    get_input_shm_name,
    get_output_shm_name,
)
from src.detectors.remote_detector import RemoteObjectDetector

logger = logging.getLogger(__name__)


class DetectorPool:
    """
    单模型检测器进程池

    管理一个 model_type 的多个检测器进程,所有进程共享同一个 detection_queue。
    相同 model_type 的相机共享检测器进程池（节约资源）。
    """

    def __init__(
        self,
        model_type: str,
        detector_config: dict,
        class_names: list,
        num_detectors: int = 3,
        input_shape: tuple = (1, 640, 640, 3),
        task_id: str = "",
    ):
        """
        Args:
            model_type: 模型类型（如 "yolov11", "yolov8"）
            detector_config: 检测器配置 dict（传给 LocalObjectDetector）
            class_names: 类别名称列表
            num_detectors: 检测器进程数量（默认3）
            input_shape: 输入共享内存形状 (1, H, W, 3)
            task_id: 任务ID（参与共享内存命名，确保同 camera 多任务各自独立 SHM）
        """
        self.model_type = model_type
        self.detector_config = detector_config
        self.class_names = class_names
        self.num_detectors = num_detectors
        self.input_shape = input_shape
        self.task_id = task_id

        # 共享队列: 所有检测器进程从同一个队列获取任务
        self.detection_queue: mp.Queue = mp.Queue()

        # 输出就绪事件: {camera_id: mp.Event}
        self.out_events: dict[str, mp.Event] = {}

        # 共享内存管理
        self.input_shms: dict[str, shared_memory.SharedMemory] = {}
        self.output_shms: dict[str, shared_memory.SharedMemory] = {}

        # RemoteObjectDetector 实例: {camera_id: RemoteObjectDetector}
        self.remote_detectors: dict[str, RemoteObjectDetector] = {}

        # 检测器进程列表
        self.detectors: list[ObjectDetectProcess] = []
        self._started = False

        logger.info(
            f"[DetectorPool-{model_type}] 初始化: "
            f"num_detectors={num_detectors}, input_shape={input_shape}"
        )

    def register_cameras(self, camera_ids: list[str]) -> dict[str, RemoteObjectDetector]:
        """
        注册相机,创建输入/输出共享内存和 RemoteObjectDetector

        必须在 start_detectors() 之前调用。检测器进程启动时会连接所有已注册相机的共享内存。

        Args:
            camera_ids: 相机ID列表

        Returns:
            {camera_id: RemoteObjectDetector}
        """
        result = {}

        for cam_id in camera_ids:
            # 防御性检查：跳过非法的 camera_id
            # - 非字符串类型（列表、字典等）
            # - 空字符串
            # - 字符串 "[]" 或 "None"（非法值）
            if not isinstance(cam_id, str):
                logger.warning(
                    f"[DetectorPool-{self.model_type}] 跳过非字符串 camera_id: {cam_id!r} (类型: {type(cam_id).__name__})"
                )
                continue
            cam_id = cam_id.strip()
            if not cam_id or cam_id in ('[]', 'None'):
                logger.warning(
                    f"[DetectorPool-{self.model_type}] 跳过非法 camera_id 值: {cam_id!r}"
                )
                continue
            # 如果已注册（同一相机被多个任务使用）,返回现有实例
            if cam_id in self.remote_detectors:
                logger.info(
                    f"[DetectorPool-{self.model_type}] 相机 {cam_id} 已注册,复用现有实例"
                )
                result[cam_id] = self.remote_detectors[cam_id]
                continue

            # 创建输出就绪事件
            self.out_events[cam_id] = mp.Event()

            # 创建输入共享内存 (1, H, W, 3) uint8
            in_shm_name = get_input_shm_name(self.model_type, cam_id, self.task_id)
            input_size = int(np.prod(self.input_shape))  # uint8 每元素1字节

            # Windows 上共享内存可能已存在（异常退出未清理）,尝试复用或重建
            try:
                in_shm = shared_memory.SharedMemory(name=in_shm_name, create=True, size=input_size)
            except FileExistsError:
                try:
                    in_shm = shared_memory.SharedMemory(name=in_shm_name, create=False)
                    if in_shm.size != input_size:
                        in_shm.close()
                        in_shm.unlink()
                        in_shm = shared_memory.SharedMemory(name=in_shm_name, create=True, size=input_size)
                except FileNotFoundError:
                    in_shm = shared_memory.SharedMemory(name=in_shm_name, create=True, size=input_size)

            self.input_shms[cam_id] = in_shm

            # 创建输出共享内存 (20, 6) float32
            out_shm_name = get_output_shm_name(self.model_type, cam_id, self.task_id)
            output_size = MAX_DETECTIONS * OUTPUT_FIELDS * 4  # float32 = 4字节

            try:
                out_shm = shared_memory.SharedMemory(name=out_shm_name, create=True, size=output_size)
                # 新创建的SHM初始化为零，避免读取到残留数据
                np.ndarray(
                    (MAX_DETECTIONS, OUTPUT_FIELDS), dtype=np.float32, buffer=out_shm.buf
                )[:] = 0
            except FileExistsError:
                try:
                    out_shm = shared_memory.SharedMemory(name=out_shm_name, create=False)
                    if out_shm.size != output_size:
                        out_shm.close()
                        out_shm.unlink()
                        out_shm = shared_memory.SharedMemory(name=out_shm_name, create=True, size=output_size)
                        # 重新创建后初始化为零
                        np.ndarray(
                            (MAX_DETECTIONS, OUTPUT_FIELDS), dtype=np.float32, buffer=out_shm.buf
                        )[:] = 0
                    else:
                        # 复用已有SHM时也清零，确保没有残留数据
                        np.ndarray(
                            (MAX_DETECTIONS, OUTPUT_FIELDS), dtype=np.float32, buffer=out_shm.buf
                        )[:] = 0
                except FileNotFoundError:
                    out_shm = shared_memory.SharedMemory(name=out_shm_name, create=True, size=output_size)
                    np.ndarray(
                        (MAX_DETECTIONS, OUTPUT_FIELDS), dtype=np.float32, buffer=out_shm.buf
                    )[:] = 0

            self.output_shms[cam_id] = out_shm

            # 创建 RemoteObjectDetector
            remote_detector = RemoteObjectDetector(
                camera_id=cam_id,
                model_type=self.model_type,
                class_names=self.class_names,
                detection_queue=self.detection_queue,
                event=self.out_events[cam_id],
                input_shape=self.input_shape,
                task_id=self.task_id,
            )
            self.remote_detectors[cam_id] = remote_detector
            result[cam_id] = remote_detector

            logger.info(
                f"[DetectorPool-{self.model_type}] 相机 {cam_id} 注册完成 "
                f"(输入SHM: {in_shm_name}, 输出SHM: {out_shm_name})"
            )

        return result

    def start_detectors(self):
        """
        创建并启动检测器进程

        必须在所有相机注册完成后调用。检测器进程启动时会连接所有已注册相机的共享内存。
        """
        if self._started:
            logger.warning(f"[DetectorPool-{self.model_type}] 检测器进程已启动,跳过")
            return

        if not self.out_events:
            logger.warning(f"[DetectorPool-{self.model_type}] 没有注册任何相机,跳过启动")
            return

        logger.info(
            f"[DetectorPool-{self.model_type}] 启动 {self.num_detectors} 个检测器进程, "
            f"服务 {len(self.out_events)} 个相机"
        )

        for i in range(self.num_detectors):
            name = f"{self.model_type}-{i + 1}"
            proc = ObjectDetectProcess(
                name=name,
                model_type=self.model_type,
                detection_queue=self.detection_queue,
                out_events=self.out_events,
                detector_config=self.detector_config,
                class_names=self.class_names,
                input_shape=self.input_shape,
                task_id=self.task_id,
            )
            self.detectors.append(proc)

        self._started = True
        logger.info(f"[DetectorPool-{self.model_type}] 检测器进程全部启动完成")

    def check_and_restart_dead(self):
        """检查并重启已死的检测器进程"""
        for proc in self.detectors:
            if proc.restart_if_dead():
                logger.warning(
                    f"[DetectorPool-{self.model_type}] 检测器进程 {proc.name} 已重启"
                )

    def stop(self):
        """停止所有检测器进程,清理共享内存"""
        logger.info(f"[DetectorPool-{self.model_type}] 正在停止...")

        # 停止检测器进程
        for proc in self.detectors:
            try:
                proc.stop()
            except Exception as e:
                logger.error(f"[DetectorPool-{self.model_type}] 停止进程失败: {e}")
        self.detectors.clear()

        # 清理 RemoteObjectDetector
        for cam_id, detector in self.remote_detectors.items():
            try:
                detector.cleanup()
            except Exception:
                pass
        self.remote_detectors.clear()

        # 清理输入共享内存
        for cam_id, shm in self.input_shms.items():
            try:
                shm.close()
                shm.unlink()
            except Exception as e:
                logger.debug(f"[DetectorPool-{self.model_type}] 清理输入SHM失败({cam_id}): {e}")
        self.input_shms.clear()

        # 清理输出共享内存
        for cam_id, shm in self.output_shms.items():
            try:
                shm.close()
                shm.unlink()
            except Exception as e:
                logger.debug(f"[DetectorPool-{self.model_type}] 清理输出SHM失败({cam_id}): {e}")
        self.output_shms.clear()

        # 清理事件
        self.out_events.clear()
        self._started = False

        logger.info(f"[DetectorPool-{self.model_type}] 已停止并清理完成")


class DetectorPoolManager:
    """
    多模型检测器进程池管理器

    按 model_type 分组管理多个 DetectorPool:
    - 相同 model_type 的相机共享一个 DetectorPool（节约资源）
    - 不同 model_type 各有独立 DetectorPool（互不干扰）

    支持两种模式:
    - 模式A(1算法多相机): 所有任务用同一 model_type,共享一个 DetectorPool
    - 模式B(多算法并行): 不同任务用不同 model_type,各有独立 DetectorPool

    使用流程:
        manager = DetectorPoolManager()
        # 1. 注册所有任务（创建共享内存和RemoteObjectDetector）
        detectors1 = manager.register_task(task_id="task1", camera_ids=["cam1","cam2"],
                                           model_type="yolov11", detector_config={...},
                                           class_names=[...])
        detectors2 = manager.register_task(task_id="task2", camera_ids=["cam3"],
                                           model_type="yolov8", detector_config={...},
                                           class_names=[...])
        # 2. 启动所有检测器进程
        manager.start_all()
        # 3. CameraWorker 使用 detectors["cam1"].detect(tensor) 进行检测
        # 4. 停止时
        manager.stop()
    """

    def __init__(self):
        self.pools: dict[str, DetectorPool] = {}
        logger.info("[DetectorPoolManager] 初始化完成")

    def register_task(
        self,
        task_id: str,
        camera_ids: list[str],
        model_type: str,
        detector_config: dict,
        class_names: list,
        num_detectors: int = 3,
        input_shape: Optional[tuple] = None,
    ) -> dict[str, RemoteObjectDetector]:
        """
        注册任务,创建共享内存和 RemoteObjectDetector

        如果该 model_type 的 DetectorPool 已存在,将相机添加到现有池中。
        否则创建新的 DetectorPool。

        Args:
            task_id: 任务ID（用于日志）
            camera_ids: 相机ID列表
            model_type: 模型类型
            detector_config: 检测器配置 dict
            class_names: 类别名称列表
            num_detectors: 检测器进程数量（仅在新创建池时生效）
            input_shape: 输入形状 (1, H, W, 3),默认 (1, 640, 640, 3)

        Returns:
            {camera_id: RemoteObjectDetector}
        """
        if input_shape is None:
            input_h = detector_config.get("input_height", 640)
            input_w = detector_config.get("input_width", 640)
            input_shape = (1, input_h, input_w, 3)

        # 创建或获取现有池
        if model_type not in self.pools:
            logger.info(
                f"[DetectorPoolManager] 创建新检测器池: "
                f"model_type={model_type}, task_id={task_id}, "
                f"num_detectors={num_detectors}"
            )
            self.pools[model_type] = DetectorPool(
                model_type=model_type,
                detector_config=detector_config,
                class_names=class_names,
                num_detectors=num_detectors,
                input_shape=input_shape,
                task_id=task_id,
            )
        else:
            logger.info(
                f"[DetectorPoolManager] 复用现有检测器池: "
                f"model_type={model_type}, task_id={task_id}"
            )

        # 注册相机
        result = self.pools[model_type].register_cameras(camera_ids)

        logger.info(
            f"[DetectorPoolManager] 任务 {task_id} 注册完成: "
            f"model_type={model_type}, cameras={camera_ids}, "
            f"返回 {len(result)} 个 RemoteObjectDetector"
        )

        return result

    def start_all(self):
        """
        启动所有检测器池的检测器进程

        必须在所有 register_task() 调用完成后调用。
        """
        logger.info(
            f"[DetectorPoolManager] 启动所有检测器进程 "
            f"({len(self.pools)} 个池)"
        )

        for model_type, pool in self.pools.items():
            pool.start_detectors()

        logger.info("[DetectorPoolManager] 所有检测器进程启动完成")

    def check_and_restart_dead(self):
        """检查并重启所有池中已死的检测器进程"""
        for pool in self.pools.values():
            pool.check_and_restart_dead()

    def get_detector(self, model_type: str, camera_id: str) -> Optional[RemoteObjectDetector]:
        """
        获取指定相机和模型的 RemoteObjectDetector

        Args:
            model_type: 模型类型
            camera_id: 相机ID

        Returns:
            RemoteObjectDetector 实例,不存在则返回 None
        """
        pool = self.pools.get(model_type)
        if pool is None:
            return None
        return pool.remote_detectors.get(camera_id)

    def stop(self):
        """停止所有检测器池,清理所有资源"""
        logger.info("[DetectorPoolManager] 正在停止所有检测器池...")

        for model_type, pool in self.pools.items():
            try:
                pool.stop()
            except Exception as e:
                logger.error(f"[DetectorPoolManager] 停止池 {model_type} 失败: {e}")

        self.pools.clear()
        logger.info("[DetectorPoolManager] 所有检测器池已停止")
