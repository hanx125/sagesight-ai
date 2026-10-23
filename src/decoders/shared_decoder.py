# -*- coding: utf-8 -*-
"""相机级共享解码服务

解决"同一相机被多个算法任务同时使用时，每个任务各拉一路 RTSP、各自 FFmpeg
解码"的重复开销。改造后：

- 每相机最多一个**解码服务进程**（由主进程 TaskProcessManager 托管，不属于
  任何任务进程树，任务停止时的进程树强杀不会误伤），进程内运行标准
  VideoDecoder，帧写入共享内存环形缓冲（task_id 固定为 SHARED_TASK_ID，
  shm 命名 sagesight_{SHARED_TASK_ID}_{camera_id}_block_{i}）。
- 多个任务的 CameraWorker 通过 SharedFrameReader 直读该环形缓冲：
  各自维护 last_seq 游标，轮询块头按序取新帧（不跳帧；消费不过来时
  环形覆盖自动跳过最旧的帧）。
- 引用计数：第一个订阅任务触发启动，最后一个释放任务触发停止+unlink；
  任务异常崩溃造成的引用泄漏在下次 acquire 时按任务进程存活状态自愈。

配置真相源：解码器参数（分辨率/帧率/块数）以第一个订阅任务为准，
后续同相机任务直接复用，分辨率差异仅在日志提示。
"""

import logging
import threading
import time
from typing import Dict, List, Optional

import multiprocessing as mp

logger = logging.getLogger(__name__)

# 共享解码器的 task_id（参与 shm 命名：sagesight___shared___{camera_id}_block_{i}）
# 使用双下划线包裹，避免与真实任务 ID（纯数字）冲突
SHARED_TASK_ID = '__shared__'


def shared_shm_prefix(camera_id: str) -> str:
    """共享解码器的 shm 前缀（与 SharedMemoryRingBuffer 命名规则一致）"""
    return f"sagesight_{SHARED_TASK_ID}_{camera_id}_block"


def build_shared_decoder_config(rtsp_url: str, decoder_cfg: dict, camera_id: str) -> dict:
    """基于任务的 decoder_config 构造共享解码器配置（task_id 固定为 SHARED_TASK_ID）"""
    return {
        'width': decoder_cfg.get('width', 640),
        'height': decoder_cfg.get('height', 640),
        'fps': decoder_cfg.get('fps', 25),
        'hwaccel_preset': decoder_cfg.get('hwaccel_preset', 'preset-dxva2'),
        'rtsp_transport': decoder_cfg.get('rtsp_transport', 'tcp'),
        'reconnect': decoder_cfg.get('reconnect', True),
        'reconnect_delay': decoder_cfg.get('reconnect_delay', 5),
        'buffer_size': decoder_cfg.get('buffer_size', 1048576),
        'frame_queue_size': decoder_cfg.get('frame_queue_size', 30),
        'use_ring_buffer': True,
        'ring_buffer_block_count': decoder_cfg.get('ring_buffer_block_count', 40),
        'index_queue_size': decoder_cfg.get('index_queue_size', 30),
        'camera_id': camera_id,
        'task_id': SHARED_TASK_ID,
        'rtsp_url': rtsp_url,
        'video_file': '',
    }


def _decoder_service_main(config: dict, stop_event) -> None:
    """解码服务进程主函数：运行 VideoDecoder 直到收到停止信号

    独立于任何任务进程树；FFmpeg 崩溃由 VideoDecoder 内部重连逻辑处理。
    """
    from src.decoders.video_decoder import VideoDecoder

    camera_id = config.get('camera_id', '?')
    try:
        decoder = VideoDecoder(config)
        decoder.start()
        logger.info(f"[SharedDecoder-{camera_id}] 共享解码服务已启动 "
                    f"(rtsp={config.get('rtsp_url', '')[:60]}, "
                    f"{config.get('width')}x{config.get('height')}@{config.get('fps')})")
        # 阻塞等待停止信号（stop_event.wait() 无超时阻塞）
        stop_event.wait()
    except Exception as e:
        logger.error(f"[SharedDecoder-{camera_id}] 解码服务异常退出: {e}", exc_info=True)
    finally:
        try:
            if 'decoder' in dir() and decoder is not None:
                decoder.stop()
        except Exception:
            pass
        logger.info(f"[SharedDecoder-{camera_id}] 共享解码服务已停止")


class SharedFrameReader:
    """共享帧读取器（消费者侧，运行在 CameraWorker 进程内）

    接口与 VideoDecoder 对齐（pipeline 只用到 get_frame_index /
    ring_buffer.read_frame_i420 / start / stop / shm_name / config）：
    - get_frame_index(): 以 last_seq 游标轮询块头，按序返回下一个新帧索引
    - ring_buffer.read_frame_i420(block_id): 直读 shm 块（Y/U/V 平面）
    - start()/stop(): no-op（生命周期由注册表管理，消费者不 unlink）
    """

    def __init__(self, camera_id: str, width: int, height: int,
                 block_count: int = 40, fps: int = 25):
        from src.utils.shared_memory import SharedMemoryRingBuffer
        from src.utils.frame_index_queue import FrameIndex

        self.camera_id = camera_id
        self.shm_name = shared_shm_prefix(camera_id)
        self.ring_buffer = SharedMemoryRingBuffer(
            camera_id=camera_id,
            block_count=block_count,
            width=width,
            height=height,
            task_id=SHARED_TASK_ID,
        )
        self.frame_shape = (height, width)
        # 与 VideoDecoder.config 对齐的最小字段集（pipeline 可能读取）
        self.config = {
            'width': width, 'height': height, 'fps': fps,
            'ring_buffer_block_count': block_count,
            'camera_id': camera_id, 'task_id': SHARED_TASK_ID,
        }

        # 游标初始化为当前最大序号：从 attach 之后的下一帧开始消费，
        # 不回放环形缓冲中已存在的旧帧
        self._last_seq = self.ring_buffer.max_sequence()
        self._frame_index_cls = FrameIndex
        # 轮询间隔：帧间隔的 1/6，兼顾延迟与 CPU（6fps→约 28ms）
        self._poll_interval = max(0.005, 1.0 / max(fps, 1) / 6.0)
        logger.info(f"[SharedFrameReader-{camera_id}] 已连接共享帧缓冲: {self.shm_name}_* "
                    f"({block_count}块 {width}x{height}), 从 seq>{self._last_seq} 开始消费")

    def get_frame_index(self):
        """按序获取下一帧索引（内部最多轮询约 0.5 秒，超时返回 None 由外层空转）"""
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            best_seq, best_block, best_ts = None, None, None
            for block_id, seq in self.ring_buffer.scan_sequences().items():
                if seq > self._last_seq and (best_seq is None or seq < best_seq):
                    best_seq, best_block = seq, block_id
            if best_seq is not None:
                header = self.ring_buffer.read_block_header(best_block)
                ts = header.timestamp_ns if header else time.time_ns()
                self._last_seq = best_seq
                return self._frame_index_cls(
                    sequence_num=best_seq, shm_block_id=best_block, timestamp_ns=ts)
            time.sleep(self._poll_interval)
        return None

    def start(self):
        """no-op：解码生命周期由 SharedDecoderRegistry 管理"""
        pass

    def stop(self):
        """no-op：绝不 unlink 共享 shm（其他消费者可能仍在使用）"""
        pass


class SharedDecoderRegistry:
    """共享解码器注册表（运行在主进程，Flask/TaskProcessManager 同进程）

    - acquire(camera_id, task_id, rtsp_url, decoder_cfg) -> dict:
        订阅该相机的共享解码器；不存在则启动解码服务进程并等待 shm 就绪。
        返回消费者连接信息（注入 camera_config 传给任务进程）。
    - release_task(task_id): 任务停止时释放其全部订阅，引用归零停止解码进程
    - reap_dead_tasks(): 清理异常退出任务的泄漏引用（acquire 时自愈）
    """

    def __init__(self):
        self._lock = threading.Lock()
        # {camera_id: {'process': Process, 'stop_event': Event, 'config': dict,
        #              'refs': {task_id: task_name}}}
        self._services: Dict[str, dict] = {}
        # {task_id: set(camera_id)} 反向索引，stop_task 时批量释放
        self._task_refs: Dict[int, set] = {}

    # ---------- 内部工具 ----------

    def _shm_ready(self, camera_id: str, width: int, height: int, timeout: float = 10.0) -> bool:
        """等待共享解码器的第一块 shm 就绪（解码服务进程创建）"""
        from multiprocessing import shared_memory
        from src.utils.shared_memory import HEADER_SIZE
        block0 = shared_shm_prefix(camera_id) + '_0'
        expected = HEADER_SIZE + height * width + (height // 2) * width
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                shm = shared_memory.SharedMemory(name=block0, create=False)
                ok = (shm.size == expected)
                shm.close()
                if ok:
                    return True
                # 大小不匹配：残留旧块，等解码进程自行重建
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.debug(f"[SharedDecoderRegistry] 检查 shm 就绪异常: {e}")
            time.sleep(0.2)
        return False

    def _start_service(self, camera_id: str, rtsp_url: str, decoder_cfg: dict) -> dict:
        """启动解码服务进程并等待就绪，返回连接信息"""
        config = build_shared_decoder_config(rtsp_url, decoder_cfg, camera_id)
        stop_event = mp.Event()
        process = mp.Process(
            target=_decoder_service_main,
            args=(config, stop_event),
            name=f"SharedDecoder-{camera_id}",
            daemon=True,  # 主进程退出时自动带走
        )
        process.start()
        logger.info(f"[SharedDecoderRegistry] 启动相机 {camera_id} 共享解码服务 (PID: {process.pid})")

        if not self._shm_ready(camera_id, config['width'], config['height']):
            raise RuntimeError(
                f"相机 {camera_id} 共享解码服务 shm 未就绪（解码进程可能启动失败）")

        return {
            'process': process,
            'stop_event': stop_event,
            'config': config,
            'refs': {},
        }

    def _stop_service(self, camera_id: str, svc: dict) -> None:
        """停止解码服务进程（优雅：stop_event → join → kill）"""
        try:
            svc['stop_event'].set()
            process = svc['process']
            process.join(timeout=8)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
            if process.is_alive():
                process.kill()
            logger.info(f"[SharedDecoderRegistry] 相机 {camera_id} 共享解码服务已停止")
        except Exception as e:
            logger.warning(f"[SharedDecoderRegistry] 停止相机 {camera_id} 解码服务异常: {e}")

    # ---------- 对外接口 ----------

    def acquire(self, camera_id: str, task_id: int, task_name: str,
                rtsp_url: str, decoder_cfg: dict) -> Optional[dict]:
        """订阅相机共享解码器，返回注入 camera_config 的连接信息

        Returns:
            {'shm_name': ..., 'width': ..., 'height': ..., 'block_count': ..., 'fps': ...}
            失败返回 None（调用方应回退到任务私有解码器）
        """
        with self._lock:
            self.reap_dead_tasks()

            svc = self._services.get(camera_id)
            if svc is not None and not svc['process'].is_alive():
                # 解码服务进程死亡：清理后重建
                logger.warning(f"[SharedDecoderRegistry] 相机 {camera_id} 解码服务已死亡，重建")
                self._stop_service(camera_id, svc)
                svc = None

            if svc is None:
                try:
                    svc = self._start_service(camera_id, rtsp_url, decoder_cfg)
                except Exception as e:
                    logger.error(f"[SharedDecoderRegistry] 启动相机 {camera_id} 解码服务失败: {e}")
                    return None
                self._services[camera_id] = svc
            else:
                existing = svc['config']
                if (existing.get('width') != decoder_cfg.get('width', 640) or
                        existing.get('height') != decoder_cfg.get('height', 640) or
                        existing.get('fps') != decoder_cfg.get('fps', 25)):
                    logger.warning(
                        f"[SharedDecoderRegistry] 相机 {camera_id} 已有共享解码器 "
                        f"({existing.get('width')}x{existing.get('height')}@{existing.get('fps')})，"
                        f"任务 {task_id} 的解码配置差异被忽略（以首个任务为准）")

            svc['refs'][task_id] = task_name
            self._task_refs.setdefault(task_id, set()).add(camera_id)

            cfg = svc['config']
            return {
                'shm_name': shared_shm_prefix(camera_id),
                'width': cfg['width'],
                'height': cfg['height'],
                'block_count': cfg['ring_buffer_block_count'],
                'fps': cfg['fps'],
            }

    def release_task(self, task_id: int) -> None:
        """任务停止：释放其全部订阅，引用归零的相机停止解码服务"""
        with self._lock:
            cameras = self._task_refs.pop(task_id, set())
            for camera_id in cameras:
                svc = self._services.get(camera_id)
                if svc is None:
                    continue
                svc['refs'].pop(task_id, None)
                if not svc['refs']:
                    # 最后一个订阅者已退出：停止解码服务并清理 shm
                    self._stop_service(camera_id, svc)
                    del self._services[camera_id]
                    logger.info(f"[SharedDecoderRegistry] 相机 {camera_id} 无订阅者，解码服务已释放")
                else:
                    logger.info(f"[SharedDecoderRegistry] 相机 {camera_id} 剩余订阅任务: "
                                f"{list(svc['refs'].values())}")

    def reap_dead_tasks(self) -> None:
        """清理异常退出任务的泄漏引用（需持有 _lock）"""
        dead = []
        for task_id in list(self._task_refs.keys()):
            # 通过引用计数与 task_processes 判断：TaskProcessManager 在
            # stop_task 中总会调 release_task，走到这里的泄漏必然是异常退出。
            # 判断依据：引用的任务不在存活任务表中（由调用方注入检查函数）。
            checker = getattr(self, '_alive_checker', None)
            if checker is not None and not checker(task_id):
                dead.append(task_id)
        for task_id in dead:
            logger.warning(f"[SharedDecoderRegistry] 检测到任务 {task_id} 异常退出，释放其共享解码订阅")
            cameras = self._task_refs.pop(task_id, set())
            for camera_id in cameras:
                svc = self._services.get(camera_id)
                if svc is None:
                    continue
                svc['refs'].pop(task_id, None)
                if not svc['refs']:
                    self._stop_service(camera_id, svc)
                    del self._services[camera_id]

    def set_alive_checker(self, checker) -> None:
        """注入任务存活检查函数（TaskProcessManager 提供）：task_id -> bool"""
        self._alive_checker = checker

    def status(self) -> List[dict]:
        """当前共享解码服务状态（调试/监控用）"""
        with self._lock:
            out = []
            for camera_id, svc in self._services.items():
                out.append({
                    'camera_id': camera_id,
                    'pid': svc['process'].pid,
                    'alive': svc['process'].is_alive(),
                    'tasks': sorted(svc['refs'].values()),
                    'resolution': f"{svc['config']['width']}x{svc['config']['height']}@{svc['config']['fps']}",
                })
            return out

    def shutdown_all(self) -> None:
        """程序退出时停止全部解码服务"""
        with self._lock:
            for camera_id, svc in list(self._services.items()):
                self._stop_service(camera_id, svc)
            self._services.clear()
            self._task_refs.clear()
            logger.info("[SharedDecoderRegistry] 全部共享解码服务已停止")


# 主进程全局单例（run.py 初始化）
shared_decoder_registry = SharedDecoderRegistry()
