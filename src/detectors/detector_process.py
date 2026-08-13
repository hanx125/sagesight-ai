"""
检测器进程模块

核心组件:
- LocalObjectDetector: 包装现有检测器后端(RKNN/TensorRT/ONNX),提供 detect_raw() 接口
- run_detector: 检测器进程主函数,从队列获取 camera_id,读输入SHM,检测,写输出SHM
- ObjectDetectProcess: 进程管理类(start/stop/restart)

通信机制:
- CameraWorker → 检测器进程: detection_queue.put(camera_id) [只传字符串]
- 检测器进程 → CameraWorker: out_events[camera_id].set() + 输出SHM
- 输入SHM: (1, H, W, 3) uint8  [CameraWorker 写,检测器读]
- 输出SHM: (20, 6) float32     [检测器写,CameraWorker 读]
  输出格式: [class_id, score, x1, y1, x2, y2] 每行一个检测

设计说明:
- LocalObjectDetector 包装 src/detectors/factory.create_detector() 创建的后端
- 输入转换: (1,H,W,3)uint8 → (1,3,H,W)float32 [0,1] (NHWC→NCHW + 归一化)
- 输出转换: List[Detection] → (20,6)float32
- 标签从 class_names 列表获取,不从文件加载
- 配置从 dict 读取
"""

import datetime
import logging
import multiprocessing as mp
import os
import queue
import signal
import threading
from typing import Optional

import numpy as np
from multiprocessing import shared_memory

logger = logging.getLogger(__name__)


# ============================================================
# 输入/输出共享内存规格
# ============================================================

# 每个相机每个模型的最大检测框数量
MAX_DETECTIONS = 20

# 输出格式: [class_id, score, x1, y1, x2, y2] (6个字段)
OUTPUT_FIELDS = 6


def _malloc_trim():
    """调用 glibc malloc_trim(0) 强制归还空闲堆内存给操作系统

    检测器进程频繁分配/释放 numpy 数组（输入帧、推理张量），
    glibc arena 会保留空闲 chunk 导致 RSS 持续增长。
    malloc_trim(0) 让 glibc 将 free'd chunk 还给内核（munmap/sbrk）。
    仅 Linux 有效，其他平台静默跳过。
    """
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except Exception:
        pass


# 系统内存使用率阈值（%）：超过此值时检测器进程主动退出，由 check_and_restart_dead 自动重启
# 用于释放 RKNN C 层（rknn-toolkit-lite2）可能累积的内存，Python gc/malloc_trim 无法回收
# 设 90%：给系统留 10% 余量，避免触发 OOM Killer
_MEM_USAGE_LIMIT = 90


def _get_process_rss_mb() -> float:
    """获取当前进程 RSS 内存（MB），失败返回 0.0"""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def _get_system_mem_usage_percent() -> float:
    """获取系统内存使用率（%），失败返回 0.0"""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except Exception:
        return 0.0


def get_input_shm_name(model_type: str, camera_id: str, task_id: str = "") -> str:
    """输入共享内存名称

    注意：Windows共享内存名称不支持中文和特殊字符，需要使用hash编码。
    task_id 参与命名，确保同一 camera_id 被多个任务使用时各自拥有独立 SHM。
    """
    import hashlib
    name_hash = hashlib.md5(f"{model_type}_{task_id}_{camera_id}".encode('utf-8')).hexdigest()[:16]
    return f"sagesight_det_inp_{name_hash}"


def get_output_shm_name(model_type: str, camera_id: str, task_id: str = "") -> str:
    """输出共享内存名称

    注意：Windows共享内存名称不支持中文和特殊字符，需要使用hash编码。
    task_id 参与命名，确保同一 camera_id 被多个任务使用时各自拥有独立 SHM。
    """
    import hashlib
    name_hash = hashlib.md5(f"{model_type}_{task_id}_{camera_id}".encode('utf-8')).hexdigest()[:16]
    return f"sagesight_det_out_{name_hash}"


# ============================================================
# LocalObjectDetector 本地检测器适配器
# ============================================================

class LocalObjectDetector:
    """
    本地检测器适配器

    包装当前项目的检测器后端(RKNN/TensorRT/ONNX),提供 detect_raw() 接口。

    输入: (1, H, W, 3) uint8  [从输入共享内存读取]
    输出: (20, 6) float32     [class_id, score, x1, y1, x2, y2]

    内部转换:
    - 输入: NHWC uint8 → NCHW float32 [0,1] (后端要求的格式)
    - 输出: List[Detection] → (20, 6) float32 数组
    """

    def __init__(self, detector_config: dict, class_names: list):
        """
        Args:
            detector_config: 检测器配置 dict,包含:
                - model_path: 模型路径
                - model_type: 模型类型 (yolov11n, yolov8n 等)
                - backend: 推理后端 (onnx_cuda, tensorrt, rknn 等)
                - conf_threshold: 置信度阈值
                - iou_threshold: NMS IoU 阈值
                - input_width: 模型输入宽度
                - input_height: 模型输入高度
                - 其他后端特定参数 (use_fp16, tensorrt_engine_path 等)
            class_names: 类别名称列表 ["person", "car", ...]
        """
        self.class_names = class_names
        self.detector_config = detector_config
        self.input_width = detector_config.get("input_width", 640)
        self.input_height = detector_config.get("input_height", 640)

        # 创建检测器后端
        from src.detectors.factory import create_detector

        # 构建后端配置参数
        backend_kwargs = {
            "model_path": detector_config["model_path"],
            "model_type": detector_config.get("model_type", "yolov11n"),
            "conf_threshold": detector_config.get("conf_threshold", 0.25),
            "iou_threshold": detector_config.get("iou_threshold", 0.45),
            "backend": detector_config.get("backend", "onnx_cuda"),
            "class_names": tuple(class_names),
            "input_width": self.input_width,
            "input_height": self.input_height,
        }

        # 可选的后端特定参数
        optional_params = [
            "tensorrt_engine_path",
            "tensorrt_engine_cache_path",
            "use_fp16",
            "rknn_target",
            "rknn_device_id",
            "rknn_core_mask",
            "device_id",
            "gpu_mem_limit",
            "min_box_area",
        ]
        for param in optional_params:
            if param in detector_config:
                backend_kwargs[param] = detector_config[param]

        logger.info(
            f"[LocalObjectDetector] 创建检测器后端: "
            f"backend={backend_kwargs['backend']}, "
            f"model_type={backend_kwargs['model_type']}, "
            f"model_path={backend_kwargs['model_path']}, "
            f"class_names数量={len(class_names)}, "
            f"input={self.input_width}x{self.input_height}"
        )

        # create_detector 会自动调用 load_model()
        self.detector = create_detector(**backend_kwargs)

        logger.info("[LocalObjectDetector] 检测器后端加载完成")

    def detect_raw(self, tensor_input: np.ndarray) -> np.ndarray:
        """
        执行检测,返回 (20, 6) float32 格式的原始检测结果

        Args:
            tensor_input: (1, H, W, 3) uint8 [NHWC 格式,从输入共享内存读取]

        Returns:
            (20, 6) float32: 每行 [class_id, score, x1, y1, x2, y2]
            不足 20 个检测的剩余行全为 0
        """
        # 输入格式按后端区分：
        # - RKNN 后端：归一化已烘焙进 .rknn 模型，直接传 uint8 NHWC [0,255]
        # - 其他后端（ONNX/TensorRT）：传 float32 NCHW [0,1]（需 /255 归一化）
        # 之前对所有后端统一 /255 归一化，导致 RKNN 收到 [0,1] float32，
        # 模型内部再 /255 变成 [0,0.004]，画面几乎全黑 → 无检测结果。
        backend = self.detector_config.get("backend", "onnx_cuda")
        if backend == "rknn":
            # RKNN：uint8 NHWC，不归一化
            infer_input = tensor_input
        else:
            # 其他后端：float32 NCHW [0,1]
            infer_input = np.transpose(tensor_input, (0, 3, 1, 2)).astype(np.float32)
            infer_input /= 255.0

        # 调用后端推理
        detections = self.detector.infer(infer_input)

        # 转换为 (20, 6) float32 格式
        output = np.zeros((MAX_DETECTIONS, OUTPUT_FIELDS), dtype=np.float32)

        for i, det in enumerate(detections[:MAX_DETECTIONS]):
            # Detection: bbox=(x1,y1,x2,y2), confidence, class_id, class_name
            output[i] = [
                float(det.class_id),
                float(det.confidence),
                float(det.bbox[0]),
                float(det.bbox[1]),
                float(det.bbox[2]),
                float(det.bbox[3]),
            ]

        return output

    def cleanup(self):
        """清理检测器资源"""
        try:
            if hasattr(self.detector, "unload_model"):
                self.detector.unload_model()
        except Exception as e:
            logger.warning(f"[LocalObjectDetector] 清理失败: {e}")


# ============================================================
# run_detector 检测器进程主函数
# ============================================================

def run_detector(
    name: str,
    model_type: str,
    detection_queue: mp.Queue,
    out_events: dict,
    detector_config: dict,
    class_names: list,
    input_shape: tuple,
    task_id: str = "",
    ready_event=None,
):
    """
    检测器进程主函数

    进程启动后:
    1. 创建 LocalObjectDetector（加载模型）
    2. 连接所有相机的输入/输出共享内存
    3. set ready_event 通知主进程检测器已就绪
    4. 循环:从 detection_queue 获取 camera_id → 读输入SHM → 检测 → 写输出SHM → set event

    Args:
        name: 检测器进程名称（如 "detector-yolov11-1"）
        model_type: 模型类型（用于共享内存命名）
        detection_queue: 检测任务队列（放入 camera_id 字符串）
        out_events: {camera_id: mp.Event} 输出就绪事件
        detector_config: 检测器配置 dict
        class_names: 类别名称列表
        input_shape: 输入共享内存形状 (1, H, W, 3)
        task_id: 任务ID（参与共享内存命名，确保同 camera 多任务各自独立 SHM）
        ready_event: 就绪事件（模型加载+SHM连接完成后 set，通知主进程可以启动 CameraWorker）
    """
    # 子进程日志配置：用 QueueHandler 把日志发到主进程，避免多进程写同一文件
    # Windows spawn 模式下子进程会继承主进程的 RotatingFileHandler，多进程同时
    # 写同一日志文件会触发 PermissionError [WinError 32]（文件锁冲突）
    try:
        from src.utils.log_manager import LogManager
        LogManager.get_instance().setup_worker_process()
    except Exception:
        pass

    threading.current_thread().name = f"detector:{name}"
    proc_logger = logging.getLogger(f"detector.{name}")
    proc_logger.info(f"检测器进程启动: pid={os.getpid()}, model_type={model_type}")

    # 信号处理（优雅退出）
    stop_event = mp.Event()

    def receive_signal(signal_number, frame):
        proc_logger.info(f"收到信号 {signal_number},准备退出")
        stop_event.set()

    signal.signal(signal.SIGTERM, receive_signal)
    try:
        signal.signal(signal.SIGINT, receive_signal)
    except (ValueError, AttributeError):
        # Windows 上 SIGINT 可能不支持
        pass

    # 创建本地检测器（加载模型）
    try:
        proc_logger.info(
            f"class_names 数量: {len(class_names)}, "
            f"前5个: {class_names[:5] if class_names else '空'}"
        )
        object_detector = LocalObjectDetector(detector_config, class_names)
    except Exception as e:
        proc_logger.error(f"检测器初始化失败: {e}", exc_info=True)
        return

    # 连接所有相机的输入/输出共享内存
    inputs = {}
    outputs = {}
    input_size = int(np.prod(input_shape))  # 字节数 (uint8)

    for cam_id in out_events.keys():
        try:
            # 输入共享内存 (1, H, W, 3) uint8
            in_shm_name = get_input_shm_name(model_type, cam_id, task_id)
            in_shm = shared_memory.SharedMemory(name=in_shm_name, create=False)
            in_np = np.ndarray(input_shape, dtype=np.uint8, buffer=in_shm.buf)
            inputs[cam_id] = {"shm": in_shm, "np": in_np}

            # 输出共享内存 (20, 6) float32
            out_shm_name = get_output_shm_name(model_type, cam_id, task_id)
            out_shm = shared_memory.SharedMemory(name=out_shm_name, create=False)
            out_np = np.ndarray(
                (MAX_DETECTIONS, OUTPUT_FIELDS), dtype=np.float32, buffer=out_shm.buf
            )
            outputs[cam_id] = {"shm": out_shm, "np": out_np}

            proc_logger.debug(f"已连接相机 {cam_id} 的共享内存")
        except FileNotFoundError:
            proc_logger.warning(f"相机 {cam_id} 的共享内存不存在,跳过")
        except Exception as e:
            proc_logger.error(f"连接相机 {cam_id} 共享内存失败: {e}")

    proc_logger.info(
        f"检测器就绪: 已连接 {len(inputs)} 个相机的共享内存,等待检测任务..."
    )

    # 通知主进程检测器已就绪（模型加载+SHM连接完成），可以启动 CameraWorker
    if ready_event is not None:
        ready_event.set()

    # 主循环
    detection_count = 0
    last_log_time = datetime.datetime.now()

    while not stop_event.is_set():
        try:
            cam_id = detection_queue.get(timeout=1)
        except queue.Empty:
            continue
        except (OSError, EOFError):
            proc_logger.info("检测队列已关闭,退出")
            break

        if cam_id not in inputs or cam_id not in outputs:
            proc_logger.warning(f"未知的 camera_id: {cam_id},跳过")
            continue

        try:
            # 从输入共享内存读取帧数据
            input_frame = inputs[cam_id]["np"]

            # 执行检测
            detections = object_detector.detect_raw(input_frame)

            # 写入输出共享内存
            outputs[cam_id]["np"][:] = detections[:]

            # 通知 CameraWorker 检测完成
            out_events[cam_id].set()

            detection_count += 1

            # 定期日志（每 100 次或每 60 秒）
            now = datetime.datetime.now()
            if detection_count % 100 == 0 or (now - last_log_time).total_seconds() > 60:
                # 归还 glibc 堆碎片，防止检测器进程 RSS 缓慢增长
                _malloc_trim()

                # 系统内存监控：使用率超过阈值主动退出，由 check_and_restart_dead 自动重启
                # 用于释放 RKNN C 层可能累积的内存（gc/malloc_trim 无法回收）
                _mem_usage = _get_system_mem_usage_percent()
                _rss = _get_process_rss_mb()
                if _mem_usage > _MEM_USAGE_LIMIT:
                    proc_logger.warning(
                        f"系统内存使用率={_mem_usage:.1f}% 超过阈值 {_MEM_USAGE_LIMIT}%,"
                        f"本进程RSS={_rss:.1f}MB,主动退出等待重启 "
                        f"(已处理 {detection_count} 次检测, model_type={model_type})"
                    )
                    break

                proc_logger.debug(
                    f"已处理 {detection_count} 次检测 (model_type={model_type}, "
                    f"系统内存={_mem_usage:.1f}%, RSS={_rss:.1f}MB)"
                )
                last_log_time = now

        except Exception as e:
            error_msg = str(e)
            proc_logger.error(f"检测失败 (camera_id={cam_id}): {e}", exc_info=True)
            # 即使失败也要 set event,避免 CameraWorker 永久等待
            try:
                out_events[cam_id].set()
            except Exception:
                pass

            # CUDA 错误（handle 损坏/执行失败）后会话不可恢复，退出进程让监控重启
            # 否则后续每次推理都会重复失败，检测器变成空转
            cuda_error_keywords = [
                'cudaErrorInvalidResourceHandle',
                'CUDNN_STATUS_EXECUTION_FAILED',
                'CUDA error',
                'out of memory',
                'cudaErrorLaunchFailure',
            ]
            if any(kw in error_msg for kw in cuda_error_keywords):
                proc_logger.error(
                    f"检测到不可恢复的 CUDA 错误,退出进程等待监控重启: {error_msg}"
                )
                break

    # 清理
    proc_logger.info("检测器进程正在清理资源...")
    object_detector.cleanup()

    for cam_id, item in inputs.items():
        try:
            item["shm"].close()
        except Exception:
            pass
    for cam_id, item in outputs.items():
        try:
            item["shm"].close()
        except Exception:
            pass

    proc_logger.info(f"检测器进程已退出 (共处理 {detection_count} 次检测)")


# ============================================================
# ObjectDetectProcess 进程管理类
# ============================================================

class ObjectDetectProcess:
    """
    检测器进程管理类

    封装检测器进程的启动、停止、重启逻辑。
    每个 ObjectDetectProcess 对应一个检测器进程。
    """

    def __init__(
        self,
        name: str,
        model_type: str,
        detection_queue: mp.Queue,
        out_events: dict,
        detector_config: dict,
        class_names: list,
        input_shape: tuple,
        task_id: str = "",
        ready_event=None,
    ):
        """
        Args:
            name: 进程名称（如 "detector-yolov11-1"）
            model_type: 模型类型
            detection_queue: 检测任务队列
            out_events: {camera_id: mp.Event} 输出就绪事件
            detector_config: 检测器配置
            class_names: 类别名称列表
            input_shape: 输入共享内存形状 (1, H, W, 3)
            task_id: 任务ID（参与共享内存命名，确保同 camera 多任务各自独立 SHM）
            ready_event: 就绪事件（模型加载完成后 set，None 时表示不等待）
        """
        self.name = name
        self.model_type = model_type
        self.out_events = out_events
        self.detection_queue = detection_queue
        self.detector_config = detector_config
        self.class_names = class_names
        self.input_shape = input_shape
        self.task_id = task_id
        self.ready_event = ready_event

        self.detect_process: Optional[mp.Process] = None
        self.start_or_restart()

    def stop(self):
        """停止检测器进程"""
        if self.detect_process and self.detect_process.exitcode is not None:
            # 进程已自行退出
            return

        if self.detect_process:
            logger.info(f"[{self.name}] 正在停止检测器进程...")
            self.detect_process.terminate()
            self.detect_process.join(timeout=30)

            if self.detect_process.exitcode is None:
                logger.warning(f"[{self.name}] 进程未响应 terminate,强制 kill")
                self.detect_process.kill()
                self.detect_process.join()

            logger.info(f"[{self.name}] 检测器进程已停止")

    def start_or_restart(self):
        """启动或重启检测器进程"""
        if self.detect_process is not None and self.detect_process.is_alive():
            self.stop()

        logger.info(f"[{self.name}] 启动检测器进程 (model_type={self.model_type})")

        # 仅在重启时（detect_process 不为 None）创建新的 ready_event
        # 首次启动时使用 __init__ 传入的 event，保持与 DetectorPool.ready_events 引用一致
        if self.detect_process is not None and self.ready_event is not None:
            self.ready_event = mp.Event()

        self.detect_process = mp.Process(
            target=run_detector,
            name=f"detector:{self.name}",
            args=(
                self.name,
                self.model_type,
                self.detection_queue,
                self.out_events,
                self.detector_config,
                self.class_names,
                self.input_shape,
                self.task_id,
                self.ready_event,
            ),
        )
        self.detect_process.daemon = True
        self.detect_process.start()

    def is_alive(self) -> bool:
        """检测器进程是否存活"""
        return self.detect_process is not None and self.detect_process.is_alive()

    def restart_if_dead(self) -> bool:
        """
        如果进程已死,重启

        Returns:
            bool: 是否发生了重启
        """
        if not self.is_alive():
            logger.warning(
                f"[{self.name}] 检测器进程已死 (exitcode={self.detect_process.exitcode}),重启..."
            )
            self.start_or_restart()
            return True
        return False
