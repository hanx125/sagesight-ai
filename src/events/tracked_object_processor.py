"""
跟踪对象事件处理进程

核心职责:
- 作为独立进程运行,消费 detected_objects_queue
- 维护每个相机的 CameraState（跟踪对象状态、事件触发）
- 通过事件回调生成事件（start / update / end / autotrack）
- 通过共享内存回读 YUV 帧,生成快照和 GIF
- 通过 HTTP API 推送 AI 视频流元数据
- 通过 PTZMetrics 共享状态驱动 PTZ 自动跟踪（与 ptz_service.py 配合）

数据流:
  CameraWorker 进程
    └── detected_objects_queue.put((
          camera_id, frame_time, tracked_objects_dict,
          motion_boxes, regions, shm_block_id, shm_name
        ))
  TrackedObjectProcessor 进程
    ├── 从 queue 获取数据
    ├── CameraState.update() → 触发事件回调
    ├── "start" 回调 → EventOutput.emit("track_start", ...)
    ├── "update" 回调 → EventOutput.emit("track_update", ...)
    ├── "end" 回调 → EventOutput.emit("track_end", ...) + GIF 生成
    ├── "autotrack" 回调 → 更新 PTZMetrics 共享状态
    │   └── PTZ 控制进程（基于 src/cameras/ptz_service.py 的 PTZController）
    │       读取 PTZMetrics 状态执行 ONVIF PTZ 控制
    └── AI 视频流推送（HTTP API）

设计说明:
- 使用 Dict 配置
- 保留 PTZ autotracker 接口:通过 PTZMetrics 共享状态与项目
  src/cameras/ptz_service.py 的 PTZController 协同工作
  - 本进程只负责写入 PTZMetrics（目标位置、跟踪激活状态）
  - 实际 PTZ ONVIF 控制由独立 PTZ 控制进程/线程完成
- 帧回读使用 SharedMemoryRingBuffer / read_frame_from_shared_memory
- 事件输出使用 EventOutput
- AI 视频流推送使用 HTTP API（端口从配置读取）
"""

import base64
import gc
import logging
import multiprocessing as mp
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional, Tuple

import cv2
import numpy as np
import psutil

from src.events.camera_state import CameraState

logger = logging.getLogger(__name__)


def _get_process_rss_mb() -> float:
    """获取当前进程 RSS 内存（MB），失败返回 0.0"""
    try:
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def _malloc_trim():
    """调用 glibc malloc_trim(0) 强制归还空闲堆内存给操作系统

    gc.collect() 只回收 Python 对象，不通知 glibc 归还碎片。
    malloc_trim(0) 让 glibc 将 free'd chunk 还给内核（munmap/sbrk），
    对频繁分配/释放 numpy 数组的进程效果显著。
    仅 Linux 有效，其他平台静默跳过。
    """
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except Exception:
        pass


class TrackedObjectProcessor(mp.Process):
    """
    跟踪对象事件处理进程

    独立进程,消费 detected_objects_queue,管理事件生命周期。
    每个 camera_id 对应一个 CameraState 和 EventOutput 实例。
    """

    def __init__(
        self,
        camera_configs: dict,
        detected_objects_queue: mp.Queue,
        stop_event: mp.Event,
        ai_stream_config: Optional[dict] = None,
        ai_stream_enabled_state: Optional[dict] = None,
        ptz_metrics: Optional[dict] = None,
        ready_event: Optional[mp.Event] = None,
    ):
        """
        Args:
            camera_configs: {camera_id: {
                'frame_shape': (height, width),
                'threshold': float,              # 检测分数阈值
                'stationary_threshold': int,     # 静止判断阈值
                'zones_config': list,            # Zone 配置
                'event_config': dict,            # EventOutput 配置
                'task_id': str,                  # 任务ID
                'algorithm': str,                # 算法名称
                'class_names': list,             # 类别名称列表
            }}
            detected_objects_queue: 检测结果队列
            stop_event: 停止事件
            ai_stream_config: AI 视频流推送配置
                {
                    'interval': int,            # 推送间隔（帧）
                    'api_url': str,             # HTTP API 地址
                }
            ai_stream_enabled_state: 跨进程共享的 AI 流启用状态 dict（Manager dict 代理）
                {camera_id: bool}
                由前端 /api/ai-stream/enable|disable API 控制，
                本进程读取此状态决定是否推送 AI 帧到前端。
                默认 False（不推送），前端点击 AI 播放后设为 True。
            ptz_metrics: PTZ 自动跟踪共享状态（可选）
                {camera_id: PTZMetrics}
                本进程通过 "autotrack" 回调写入目标位置和跟踪激活状态,
                实际 PTZ ONVIF 控制由独立 PTZ 控制进程基于
                src/cameras/ptz_service.py 的 PTZController 完成。
            ready_event: 就绪事件（可选）
                子进程完成初始化（CameraState/EventOutput/FrameCache/线程池创建完毕）
                进入主循环前 set，主进程等待此事件后再启动 CameraWorker，
                避免 CameraWorker 投递的帧在 processor 就绪前填满队列。
        """
        super().__init__(name="TrackedObjectProcessor")

        self.camera_configs = camera_configs
        self.detected_objects_queue = detected_objects_queue
        self.stop_event = stop_event
        self.ready_event = ready_event
        self.ai_stream_config = ai_stream_config or {}
        # 显示配置（框颜色、线宽、是否显示检测框/跟踪框/轨迹等）
        self.display_config = self.ai_stream_config.get('display_config', {})

        # AI 视频流启用状态（跨进程共享，由前端 enable/disable API 控制）
        # 默认 None 或空 dict 时，所有摄像机不推送 AI 流
        self.ai_stream_enabled_state = ai_stream_enabled_state

        # AI 流文件标记目录（替代 Manager dict 代理）
        # 主进程通过创建/删除 data/ai_stream_flags/{camera_id}.flag 控制启用状态，
        # 本进程通过 os.path.exists 检查标记文件决定是否推送 AI 帧。
        self.ai_stream_flag_dir = self.ai_stream_config.get('flag_dir')

        self.ptz_metrics: dict = ptz_metrics or {}

        # 运行时状态（在 run() 中初始化）
        self.camera_states: dict[str, CameraState] = {}
        self.event_outputs: dict[str, object] = {}
        self.frame_caches: dict[str, object] = {}  # {camera_id: FrameCache}
        self.active_events: dict[str, dict] = {}  # {camera_id: {obj_id: event_info}}

        # 事件层合并：宽限期内待结束的事件
        # 解决跟踪 ID switch 导致的重复事件：目标消失时不立即结束事件，
        # 移入 pending_end_events 等待宽限期。若宽限期内有同 label+bbox 相近的新目标出现，
        # 认为是同一目标的 ID 切换，复用原 event_id 延续事件；超时则真正结束。
        # {camera_id: {track_id: {event_id, label, bbox, end_frame_time, end_wall_time, obj_dict, active_event}}}
        self.pending_end_events: dict[str, dict[str, dict]] = {}
        # 宽限期（秒）：目标消失后多久才真正结束事件
        # 配合 max_age=45（约1.5秒），总容忍时间约 4.5 秒，覆盖大多数短时遮挡场景
        self.event_grace_period = 3.0

        # GIF 生成线程池（耗时IO操作）
        self.gif_executor: Optional[ThreadPoolExecutor] = None

        # AI 视频流异步推送线程池
        self.ai_stream_executor: Optional[ThreadPoolExecutor] = None

        # AI 视频流推送帧计数器
        self.ai_stream_frame_counters: dict[str, int] = {}

        # 当前帧（主循环读一次 shm，回调/frame_cache/AI流共用，避免重复读 shm）
        self._current_frame: Optional[np.ndarray] = None
        # 当前帧是否与检测框对齐（True=帧序号匹配，False=降级使用最新帧）
        self._current_frame_aligned: bool = True

        # AI 流 per-camera 背压锁（防止线程池积压，丢旧帧）
        self.ai_stream_inflight_locks: dict[str, threading.Lock] = {}

        # ✅ 复用 HTTP 连接池：AI 流每帧推送（6fps×N相机），不复用 Session 会导致
        # urllib3 PoolManager/HTTPAdapter 反复创建销毁，内存碎片持续增长（~6MB/min）
        self._http_session: Optional[Any] = None  # requests.Session，在 run() 中初始化

        # PTZ 自动跟踪:每个相机当前跟踪的目标 obj_id（None 表示无跟踪目标）
        self.ptz_tracked_object: dict[str, Optional[str]] = {}

        logger.info(
            f"[TrackedObjectProcessor] 初始化: "
            f"cameras={len(camera_configs)}, "
            f"ptz_cameras={len(self.ptz_metrics)}, "
            f"ai_stream_enabled={bool(self.ai_stream_config)}"
        )

    def run(self):
        """进程主函数"""
        # 子进程日志配置：用 QueueHandler 把日志发到主进程，避免多进程写同一文件
        # Windows spawn 模式下子进程会继承主进程的 RotatingFileHandler，多进程同时
        # 写同一日志文件会触发 PermissionError [WinError 32]（文件锁冲突）
        try:
            from src.utils.log_manager import LogManager
            LogManager.get_instance().setup_worker_process()
        except Exception:
            pass

        proc_logger = logging.getLogger("tracked_object_processor")
        proc_logger.info(f"事件处理进程启动: pid={os.getpid()}")

        # 信号处理
        def receive_signal(signal_number, frame):
            proc_logger.info(f"收到信号 {signal_number},准备退出")
            self.stop_event.set()

        signal.signal(signal.SIGTERM, receive_signal)
        try:
            signal.signal(signal.SIGINT, receive_signal)
        except (ValueError, AttributeError):
            pass

        # 初始化 CameraState 和 EventOutput
        self._init_components(proc_logger)

        # GIF 线程池
        self.gif_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="GIF-Gen"
        )

        # AI 视频流异步推送线程池
        self.ai_stream_executor = ThreadPoolExecutor(
            max_workers=6, thread_name_prefix="AI-Stream"
        )

        # FrameCache 异步写入线程池（JPEG 编码 ~2-5ms 移出主循环）
        # per-camera 背压锁防止积压：上一帧编码中则丢弃本帧
        self.frame_cache_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="FrameCache"
        )
        self.frame_cache_inflight_locks: dict[str, threading.Lock] = {}

        # 事件规则去重（事件管理功能，独立于大模型，不消耗 token）
        self.rule_deduper = None
        self._init_rule_dedup(proc_logger)

        # 大模型事件描述+语义去重线程池（异步调用，不阻塞事件管道）
        # 大模型 API 响应 1-3 秒，必须异步执行
        self.llm_executor: Optional[ThreadPoolExecutor] = None
        self.llm_describer = None
        self.llm_deduplicator = None
        self._init_llm(proc_logger)

        # ✅ 初始化复用的 HTTP Session（避免每次 requests.post 创建新 PoolManager）
        from requests import Session
        from requests.adapters import HTTPAdapter
        self._http_session = Session()
        adapter = HTTPAdapter(
            pool_connections=10, pool_maxsize=20, max_retries=0
        )
        self._http_session.mount('http://', adapter)
        self._http_session.mount('https://', adapter)

        # AI 视频流配置
        ai_stream_interval = self.ai_stream_config.get("interval", 10)

        proc_logger.info(
            f"事件处理进程就绪: "
            f"camera_states={len(self.camera_states)}, "
            f"ai_stream_interval={ai_stream_interval}"
        )

        # 通知主进程：初始化完成，可以启动 CameraWorker 投递数据
        # 主进程在 start() 中等待此事件，避免队列在 processor 就绪前被填满
        if self.ready_event is not None:
            self.ready_event.set()

        # 主循环
        processed_count = 0
        last_stats_time = time.time()
        # 内存管理：定期 gc.collect() 回收循环引用和大对象
        # Python 默认的分代回收不够及时，长时间运行后图像数组碎片会持续推高 RSS
        last_gc_time = time.time()
        # gc.collect() 执行间隔（秒）：每 5 分钟强制回收一次
        _GC_INTERVAL = 300
        # 诊断计数器：统计跟踪对象与事件生命周期
        # tracked_sum：累计接收到的 tracked_objects 数量（跨所有帧所有相机）
        # start_triggered / end_triggered：start/end 回调触发次数
        # event_durations：每次事件的持续秒数（用于判断事件是否被过早结束）
        tracked_sum = 0
        self._start_triggered_count = 0
        self._end_triggered_count = 0
        self._event_durations = []
        # 诊断计数器：统计 shm 帧回读和 FrameCache 写入成功率
        self._shm_read_total = 0       # _read_frame_from_shm 调用总次数
        self._shm_read_success = 0     # 成功返回非 None 的次数
        self._shm_read_misaligned = 0  # 降级使用最新帧的次数（帧序号不匹配，帧与框可能错位）
        self._frame_cache_push_total = 0   # frame_cache.push 调用次数
        self._frame_cache_push_success = 0  # 实际写入缓存次数（采样通过+编码成功）
        self._max_queue_size = 0       # 队列最大积压深度

        while not self.stop_event.is_set():
            try:
                # 从队列获取数据（1秒超时,便于检查 stop_event）
                data = self.detected_objects_queue.get(timeout=1)
                # 诊断：记录队列积压深度
                qsize = self.detected_objects_queue.qsize()
                if qsize > self._max_queue_size:
                    self._max_queue_size = qsize
            except Exception:
                # 队列为空或出错
                continue

            try:
                # 记录本帧跟踪对象数量（_process_detection_data 内会更新 self._last_tracked_count）
                self._last_tracked_count = 0
                self._process_detection_data(data)
                tracked_sum += self._last_tracked_count
                processed_count += 1

                # 定期统计日志（每 60 秒）
                now = time.time()
                if now - last_stats_time > 60:
                    avg_dur = (
                        sum(self._event_durations) / len(self._event_durations)
                        if self._event_durations else 0
                    )
                    # 诊断：shm 回读成功率、对齐率与 FrameCache 写入成功率
                    shm_rate = (
                        self._shm_read_success / max(1, self._shm_read_total) * 100
                    )
                    misalign_rate = (
                        self._shm_read_misaligned / max(1, self._shm_read_success) * 100
                    )
                    cache_rate = (
                        self._frame_cache_push_success / max(1, self._frame_cache_push_total) * 100
                    )
                    # 内存监控：采集当前进程 RSS（Resident Set Size）
                    _current_rss_mb = _get_process_rss_mb()
                    proc_logger.info(
                        f"已处理 {processed_count} 帧检测数据, "
                        f"活跃事件: {sum(len(v) for v in self.active_events.values())}, "
                        f"累计跟踪对象: {tracked_sum}, "
                        f"start触发: {self._start_triggered_count}, "
                        f"end触发: {self._end_triggered_count}, "
                        f"平均事件持续: {avg_dur:.2f}s, "
                        f"shm回读成功率: {shm_rate:.1f}% ({self._shm_read_success}/{self._shm_read_total}), "
                        f"帧未对齐率: {misalign_rate:.1f}% ({self._shm_read_misaligned}/{self._shm_read_success}), "
                        f"缓存写入成功率: {cache_rate:.1f}% ({self._frame_cache_push_success}/{self._frame_cache_push_total}), "
                        f"队列最大积压: {self._max_queue_size}, "
                        f"内存RSS: {_current_rss_mb:.1f}MB"
                    )
                    last_stats_time = now

                # 定期 gc.collect()：回收循环引用和滞留的图像数组
                # Python 默认分代回收对大对象（numpy 数组）不够及时，
                # 长时间运行会导致 RSS 持续增长（即使对象已无引用）
                if now - last_gc_time > _GC_INTERVAL:
                    collected = gc.collect()
                    # gc 后调用 malloc_trim(0) 强制 glibc 归还碎片堆内存给内核
                    # 仅回收 Python 对象不足以降低 RSS，glibc arena 会保留空闲 chunk
                    _malloc_trim()
                    # 采集 gc 后的内存，便于对比回收效果
                    _post_gc_rss_mb = _get_process_rss_mb()
                    proc_logger.info(
                        f"gc.collect() 回收对象: {collected}, "
                        f"内存RSS: {_post_gc_rss_mb:.1f}MB "
                        f"(gc前: {_current_rss_mb:.1f}MB, 差值: {_current_rss_mb - _post_gc_rss_mb:+.1f}MB)"
                    )
                    last_gc_time = now

            except Exception as e:
                proc_logger.error(f"处理检测数据失败: {e}", exc_info=True)

        # 清理
        proc_logger.info("事件处理进程正在清理...")

        # 关闭 GIF 线程池
        if self.gif_executor:
            self.gif_executor.shutdown(wait=False)

        # 关闭 FrameCache 异步写入线程池
        if self.frame_cache_executor:
            self.frame_cache_executor.shutdown(wait=False)

        # 关闭 AI 流推送线程池
        if self.ai_stream_executor:
            self.ai_stream_executor.shutdown(wait=False)

        # 关闭大模型线程池
        if self.llm_executor:
            self.llm_executor.shutdown(wait=False)

        # ✅ 关闭 HTTP Session（释放连接池）
        if self._http_session is not None:
            try:
                self._http_session.close()
            except Exception:
                pass

        self._cleanup(proc_logger)
        proc_logger.info(f"事件处理进程已退出 (共处理 {processed_count} 帧)")

    def _init_rule_dedup(self, proc_logger):
        """初始化事件规则去重（事件管理功能，独立于大模型，不消耗 token）

        配置来源为 system_config.yaml 的 event_dedup 段，不受大模型
        总开关/大模型配置影响，总是初始化。
        """
        try:
            from src.system.system_config_loader import get_event_dedup_config
            from src.events.rule_dedup import RuleDeduper
            self.rule_deduper = RuleDeduper(get_event_dedup_config())
            proc_logger.info(
                f"事件规则去重已初始化: enabled={self.rule_deduper.enabled}, "
                f"window={self.rule_deduper.window_seconds}s, "
                f"iou={self.rule_deduper.iou_threshold}"
            )
        except Exception as e:
            proc_logger.warning(f"事件规则去重初始化失败: {e}")

    def _init_llm(self, proc_logger):
        """初始化大模型事件描述+语义去重服务

        先检查全局总开关（llm_enabled）：关闭时不初始化任何大模型服务，
        避免产生不必要的 token 费用。再加载默认 LLM 配置，若未配置或
        未启用则跳过（不影响事件处理）。规则去重由事件管理独立执行，
        与本模块无关。
        """
        try:
            from src.system.system_manager import get_system_manager
            manager = get_system_manager()

            # 全局总开关：关闭则整个系统不使用大模型
            if not manager.is_llm_enabled():
                proc_logger.info("大模型功能总开关已关闭，事件描述+语义去重未启用")
                return

            config = manager.get_default_llm_config()

            if config is None or not config.enabled:
                proc_logger.info("未配置默认大模型，事件描述+语义去重未启用")
                return

            # 检查是否至少启用了一项功能
            if not config.enable_description and not config.enable_dedup:
                proc_logger.info("大模型描述和语义去重均未启用，跳过初始化")
                return

            from src.llm.event_describer import EventDescriber
            from src.llm.event_deduplicator import EventDeduplicator

            self.llm_executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="LLM"
            )

            if config.enable_description:
                self.llm_describer = EventDescriber(config)
            if config.enable_dedup:
                self.llm_deduplicator = EventDeduplicator(config)

            proc_logger.info(
                f"大模型事件处理已启用: model={config.model}, "
                f"provider={config.provider}, "
                f"description={'on' if self.llm_describer else 'off'}, "
                f"semantic_dedup={'on' if self.llm_deduplicator else 'off'}"
            )
        except Exception as e:
            proc_logger.warning(f"大模型初始化失败，事件描述+语义去重未启用: {e}")

    def _init_components(self, proc_logger):
        """初始化 CameraState、EventOutput 和 FrameCache"""
        from src.events.event_output import EventOutput
        from src.events.frame_cache import FrameCache
        from src.system.system_config_loader import (
            load_system_config, get_storage_config, get_mqtt_config
        )

        _init_start = time.time()
        proc_logger.info(f"开始初始化 {len(self.camera_configs)} 个相机的组件...")

        # 系统配置只加载一次（避免每个相机重复读文件）
        _storage_config = get_storage_config()
        _mqtt_config = get_mqtt_config()
        _system_config = load_system_config()

        for cam_id, config in self.camera_configs.items():
            # 创建 CameraState
            self.camera_states[cam_id] = CameraState(
                camera_id=cam_id,
                frame_shape=config.get("frame_shape", (640, 640)),
                threshold=config.get("threshold", 0.5),
                stationary_threshold=config.get("stationary_threshold", 10),
                zones_config=config.get("zones_config", []),
            )

            # 创建 EventOutput
            event_config = config.get("event_config", {})
            event_config["camera_id"] = cam_id
            event_config["task_id"] = config.get("task_id")
            event_config["algorithm"] = config.get("algorithm", "yolov11")

            # 注入全局系统配置（复用已加载的配置，避免重复读文件）
            event_config["storage"] = _storage_config
            event_config["mqtt"] = _mqtt_config
            event_config["system_config"] = _system_config
            event_config.setdefault("enabled", True)
            event_config.setdefault("save_to_db", True)

            # 注入 display_config 到 gif 配置，使 GIF 绘制（检测框/轨迹颜色）与
            # AI 视频流、事件图片保持一致的颜色来源（单一真相源）
            if self.display_config:
                event_config.setdefault("gif", {})["display_config"] = self.display_config

            # 必需区域：从 zones_config 的 required 标志派生（单一真相源）
            # 任一区域 required=true 时，目标必须进入必需区域才产生事件（延迟创建）
            # event_output.yaml 不再配置 capture.required_zones，避免两个来源冲突
            zones_config = config.get("zones_config", [])
            required_zone_names = [z.get("name") for z in zones_config if z.get("required")]
            if required_zone_names:
                event_config.setdefault("capture", {})["required_zones"] = required_zone_names

            self.event_outputs[cam_id] = EventOutput(event_config)

            # 创建 FrameCache（仅 GIF 启用时才缓存帧）
            gif_cfg = event_config.get("gif", {})
            if gif_cfg.get("enabled", False):
                self.frame_caches[cam_id] = FrameCache(
                    camera_id=cam_id,
                    cache_fps=gif_cfg.get("cache_fps", 10),
                    cache_retention=gif_cfg.get("cache_retention", 20.0),
                )
                proc_logger.info(
                    f"[{cam_id}] FrameCache 已创建: "
                    f"cache_fps={gif_cfg.get('cache_fps', 10)}, "
                    f"cache_retention={gif_cfg.get('cache_retention', 20.0)}s"
                )

            # 初始化活跃事件字典
            self.active_events[cam_id] = {}

            # 初始化 AI 视频流帧计数器
            self.ai_stream_frame_counters[cam_id] = 0

            # 初始化 PTZ 跟踪目标
            self.ptz_tracked_object[cam_id] = None

            # AI 视频流默认不推送，由前端点击 AI 播放后通过
            # /api/ai-stream/enable 创建 {flag_dir}/{camera_id}.flag 标记文件，
            # 本进程通过 os.path.exists 检测标记文件决定是否推送 AI 帧。

            # 注册事件回调
            self._register_callbacks(cam_id)

            proc_logger.info(
                f"[{cam_id}] CameraState 和 EventOutput 初始化完成"
                + (
                    f", PTZ 自动跟踪已启用"
                    if cam_id in self.ptz_metrics
                    else ""
                )
            )

        proc_logger.info(
            f"所有 {len(self.camera_configs)} 个相机组件初始化完成，"
            f"总耗时 {time.time()-_init_start:.2f}s"
        )

    def _register_callbacks(self, cam_id: str):
        """为相机注册事件回调"""
        state = self.camera_states[cam_id]

        # "start" 回调:新目标出现
        state.on("start", self._on_object_start)

        # "update" 回调:目标状态显著变化
        state.on("update", self._on_object_update)

        # "end" 回调:目标消失
        state.on("end", self._on_object_end)

        # 注：zone_enter / zone_exit / loitering 不再作为独立事件，
        # zone 变化由 TrackedObject 维护 current_zones / entered_zones / pending_loitering，
        # 随 update / end 事件自然透传给订阅方。

        # "autotrack" 回调:目标位置明显移动 → 更新 PTZMetrics
        # 仅在配置了 PTZMetrics 的相机上注册（基于 src/cameras/ptz_service.py）
        if cam_id in self.ptz_metrics:
            state.on("autotrack", self._on_object_autotrack)

    def _process_detection_data(self, data: tuple):
        """
        处理从队列获取的检测数据

        数据格式: (camera_id, frame_time, tracked_objects_dict,
                   motion_boxes, regions, shm_block_id, shm_name,
                   detection_results, frame_seq)
        """
        if len(data) < 7:
            logger.warning(f"检测数据格式错误,长度不足: {len(data)}")
            return

        camera_id = data[0]
        frame_time = data[1]
        tracked_objects_dict = data[2]
        motion_boxes = data[3] if len(data) > 3 else []
        regions = data[4] if len(data) > 4 else []
        shm_block_id = data[5] if len(data) > 5 else None
        shm_name = data[6] if len(data) > 6 else None
        detection_results = data[7] if len(data) > 7 else []
        # 帧序号：写入共享内存时记录的 sequence_num，回读 shm 时校验块是否已被覆盖
        frame_seq = data[8] if len(data) > 8 else None

        if camera_id not in self.camera_states:
            logger.warning(f"未知的 camera_id: {camera_id}")
            return

        # 诊断：记录本帧跟踪对象数量（供主循环统计累计值）
        self._last_tracked_count = len(tracked_objects_dict or {})

        # 保存当前帧的共享内存信息（用于事件回调时回读帧）
        self._current_shm_info = {
            "shm_block_id": shm_block_id,
            "shm_name": shm_name,
            "frame_time": frame_time,
            "frame_seq": frame_seq,
        }

        # 保存当前帧的检测数据（供事件回调绘制检测框使用，与 AI 视频流绘制保持一致）
        self._current_tracked_objects = tracked_objects_dict or {}
        self._current_motion_boxes = motion_boxes or []
        self._current_regions = regions or []
        self._current_detection_results = detection_results or []

        # 按需读 shm：仅 GIF 帧缓存启用时预读（FrameCache 必须每帧写入）
        # 事件回调（start/update/end）和 AI 流推送均已有 self._current_frame 为 None
        # 时的兜底读 shm 逻辑，无需每帧预读。
        # 严格对齐：同步读取当前帧，保证检测框与画面一致（不使用管线化避免错位）
        # 连接缓存优化：_read_frame_from_shm 内部已缓存 SharedMemory 连接对象，
        #   不再每次新建连接，回读耗时从 5-8ms 降到 ~2ms
        frame_cache = self.frame_caches.get(camera_id)
        need_frame = frame_cache is not None
        # _read_frame_from_shm 返回 (frame, aligned)，aligned=False 表示降级使用最新帧
        self._current_frame, self._current_frame_aligned = (
            self._read_frame_from_shm(camera_id) if need_frame else (None, True)
        )
        # 诊断：统计 shm 回读成功率与对齐情况
        if need_frame:
            self._shm_read_total += 1
            if self._current_frame is not None:
                self._shm_read_success += 1
                if not self._current_frame_aligned:
                    self._shm_read_misaligned += 1

        # 更新 CameraState（触发事件回调，回调复用 self._current_frame）
        self.camera_states[camera_id].update(
            frame_time=frame_time,
            current_tracked_objects=tracked_objects_dict,
            motion_boxes=motion_boxes,
            regions=regions,
        )

        # 事件层合并：清理超宽限期的 pending_end 事件（每帧检查，开销可忽略）
        # _on_object_end 时事件进入宽限期，超时则在此处真正结束
        # 注意：必须在 CameraState.update 之后调用，确保本帧的 end 回调已先入 pending
        if self.pending_end_events:
            self._cleanup_expired_pending_ends(frame_time)

        # 帧缓存：异步写入（JPEG 编码 ~2-5ms 移出主循环）
        # 主循环只做采样判断 + per-camera 背压锁检查 + submit，
        # JPEG 编码和 deque.append 在 frame_cache_executor 线程池异步执行
        if frame_cache is not None and self._current_frame is not None:
            self._async_push_frame_cache(camera_id, frame_cache, frame_time, self._current_frame)

        # AI 视频流推送（复用已读帧）
        self._maybe_push_ai_stream(
            camera_id, frame_time, tracked_objects_dict,
            motion_boxes, regions, detection_results,
            frame=self._current_frame,
        )

        # 清理当前帧引用
        self._current_frame = None
        self._current_frame_aligned = True

    def _emit_tracking_start(self, camera_id: str, obj_dict: dict, wall_time: float) -> Optional[str]:
        """创建跟踪事件（type=tracking, status=tracking_start）并返回 event_id

        由 _on_object_start（立即创建）和 _on_object_update（延迟创建）共用。
        wall_time 为事件开始时间（系统 Unix 时间戳），由调用方提供以保证事件记录的
        start_time 与内存 active_events 的 start_wall_time 一致。
        未通过 should_send 过滤时返回 None。
        """
        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return None

        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        if not event_output.should_send(class_name, confidence):
            return None

        # 采集开始快照（复用主循环已读帧），绘制检测框（与 AI 视频流保持一致）
        image = None
        if event_output.capture_save_full_on_start:
            image = self._current_frame
            image_aligned = self._current_frame_aligned
            if image is None:
                # 兜底读 shm，返回 (frame, aligned)
                image, image_aligned = self._read_frame_from_shm(camera_id)
            if image is not None:
                if not image_aligned:
                    logger.warning(
                        f"[{camera_id}] 事件开始快照帧未对齐(降级使用最新帧): "
                        f"track_id={obj_dict.get('id')}, "
                        f"检测框可能滞后于画面"
                    )
                image = self._prepare_event_image(camera_id, image)

        track_id = obj_dict.get("id", "")
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        event_data = {
            "track_id": track_id,
            "class_name": class_name,
            "confidence": confidence,
            "bbox": bbox,
            "center": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
            "start_time": wall_time,
            "status": "tracking_start",
            "current_zones": obj_dict.get("current_zones", []),
            "entered_zones": obj_dict.get("entered_zones", []),
            "pending_loitering": obj_dict.get("pending_loitering", []),
            "loitered_zones": obj_dict.get("loitered_zones", []),
        }

        return event_output.emit(
            event_type=self._derive_event_type(obj_dict),
            data=event_data,
            image=image,
            frame_id=obj_dict.get("frame_time", 0),
            lifecycle=True,  # 参与去重状态机：创建阶段不推 MQTT，状态切换为最终事件后再推送
        )

    def _derive_event_type(self, obj_dict: dict) -> str:
        """从跟踪元数据派生事件类型（优先级：徘徊 > 区域 > 跟踪）

        - loitered_zones 非空 → "loitering"（徘徊检测）
        - entered_zones 非空 → "zone"（区域检测）
        - 否则              → "tracking"（目标跟踪）
        """
        if obj_dict.get("loitered_zones"):
            return "loitering"
        if obj_dict.get("entered_zones"):
            return "zone"
        return "tracking"

    def _on_object_start(self, camera_id: str, obj_dict: dict, frame_time: float):
        """新目标出现回调 - 创建跟踪事件（type=tracking, status=tracking_start）

        事件层合并：先检查 pending_end_events 是否有匹配项（同 label+bbox 相近），
        匹配则认为是 ID switch，复用原 event_id 延续事件；无匹配才创建新事件。

        必需区域过滤：延迟创建到目标首次进入必需区域（start 阶段 entered_zones 为空无法判定）。
        """
        # 诊断：统计 start 回调触发次数（无论后续是否被过滤）
        self._start_triggered_count = getattr(self, "_start_triggered_count", 0) + 1

        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return

        track_id = obj_dict.get("id", "")
        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        # ========================================
        # 事件层合并：检查是否为 ID switch（匹配 pending_end 中的事件）
        # ========================================
        matched_pending_id = self._match_pending_end_event(camera_id, class_name, bbox)
        if matched_pending_id is not None:
            pending_info = self.pending_end_events[camera_id].pop(matched_pending_id)
            # 复用原 event_id 和 start 时间，事件延续
            self.active_events[camera_id][track_id] = {
                "event_id": pending_info.get("event_id", ""),
                "start_time": pending_info.get("active_event", {}).get("start_time", frame_time),
                "start_wall_time": pending_info.get("active_event", {}).get("start_wall_time", time.time()),
                "class_name": class_name,
                "bbox": bbox,
                # ID switch 合并标记（避免宽限期内的 end_image 被误用）
                "merged_from": matched_pending_id,
            }
            logger.info(
                f"[{camera_id}] 跟踪事件合并(ID switch): "
                f"old_id={matched_pending_id} → new_id={track_id}, "
                f"label={class_name}, event_id={pending_info.get('event_id', '')[:8]}, "
                f"score={confidence:.3f}"
            )
            return

        # ========================================
        # 无匹配：创建新事件
        # ========================================

        # 必需区域过滤：延迟事件创建到目标首次进入必需区域
        # start 阶段 entered_zones 必为空（目标刚出现，尚未确认进入任何区域），无法判定
        if event_output.has_required_zones():
            self.active_events[camera_id][track_id] = {
                "event_id": None,                # None = pending（尚未进入必需区域）
                "pending_required_zones": True,
                "start_time": frame_time,        # 视频相对时间（用于持续时间和 GIF 回取）
                "start_wall_time": time.time(),  # 系统 Unix 时间戳（用于事件数据库）
                "class_name": class_name,
                "bbox": bbox,
            }
            logger.debug(
                f"[{camera_id}] 目标 {track_id} 待定（必需区域过滤），等待进入必需区域: "
                f"label={class_name}, score={confidence:.3f}"
            )
            return

        # 无必需区域：立即创建事件
        wall_time = time.time()
        event_id = self._emit_tracking_start(camera_id, obj_dict, wall_time)

        if event_id:
            self.active_events[camera_id][track_id] = {
                "event_id": event_id,
                "start_time": frame_time,       # 视频相对时间（用于持续时间和 GIF 回取）
                "start_wall_time": wall_time,   # 系统 Unix 时间戳（用于事件数据库）
                "class_name": class_name,
                "bbox": bbox,
            }

        logger.info(
            f"[{camera_id}] 跟踪事件开始: id={track_id}, label={class_name}, "
            f"score={confidence:.3f}, event_id={event_id}"
        )

    def _on_object_update(self, camera_id: str, obj_dict: dict, frame_time: float):
        """目标状态显著变化回调 - 只更新内存最佳缩略图（不保存图片到存储，不推送 WebSocket）

        单一事件模型：update 阶段不写数据库、不保存图片、不推送前端。
        只在内存中更新 best_thumbnail，等 _on_object_end 时一次性保存置信度最高的图片。
        """
        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return

        track_id = obj_dict.get("id", "")

        # 检查是否有活跃事件
        active_event = self.active_events.get(camera_id, {}).get(track_id)
        if active_event is None:
            return

        # 必需区域延迟创建：pending 事件在目标首次进入必需区域时才创建
        # start 阶段 entered_zones 为空无法判定，需在 update 阶段确认
        if active_event.get("pending_required_zones") and active_event.get("event_id") is None:
            entered_zones = obj_dict.get("entered_zones", []) or []
            if not event_output.passes_required_zones(entered_zones):
                # 仍未进入必需区域，继续等待（不更新缩略图，无 event_id）
                return
            # 首次进入必需区域 → 现在创建事件（延迟创建）
            # start_time 沿用目标出现时刻（active_event.start_wall_time），保证事件记录的时间语义正确
            wall_time = active_event.get("start_wall_time", time.time())
            event_id = self._emit_tracking_start(camera_id, obj_dict, wall_time)
            if event_id is None:
                # 创建失败（如 should_send 未通过），放弃 pending 并清理
                del self.active_events[camera_id][track_id]
                return
            active_event["event_id"] = event_id
            active_event["pending_required_zones"] = False
            logger.info(
                f"[{camera_id}] 目标 {track_id} 进入必需区域，创建延迟事件: "
                f"event_id={event_id}, entered_zones={entered_zones}"
            )
            return  # 本次 update 仅完成创建，下次 update 再更新缩略图

        event_id = active_event.get("event_id", "")

        # 生成快照（仅在 thumbnail 更新时，复用主循环已读帧）
        image = None
        if obj_dict.get("snapshot") is not None:
            image = self._current_frame
            image_aligned = self._current_frame_aligned
            if image is None:
                # 兜底读 shm，返回 (frame, aligned)
                image, image_aligned = self._read_frame_from_shm(camera_id)
            if image is not None and not image_aligned:
                logger.warning(
                    f"[{camera_id}] 事件更新缩略图帧未对齐(降级使用最新帧): "
                    f"event_id={event_id}, track_id={obj_dict.get('id')}, "
                    f"检测框可能滞后于画面"
                )
            # 绘制检测框（与 AI 视频流保持一致，best_thumbnail 也带检测框）
            image = self._prepare_event_image(camera_id, image)

        # 构建更新数据
        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        # ✅ 只更新内存最佳缩略图（不保存到存储，不推送 WebSocket）
        if image is not None:
            event_output.update_thumbnail(
                event_id, image, confidence, tuple(bbox)
            )

        # ✅ 更新数据库事件状态为 tracking_update（不推送前端，只更新数据库）
        if event_id:
            update_data = {
                "confidence": confidence,
                "bbox": bbox,
                "class_name": class_name,
                "current_zones": obj_dict.get("current_zones", []),
                "entered_zones": obj_dict.get("entered_zones", []),
                "pending_loitering": obj_dict.get("pending_loitering", []),
                "loitered_zones": obj_dict.get("loitered_zones", []),
            }
            event_output.update_event(
                event_id,
                status="tracking_update",
                data=update_data,
                image=None,
                push_websocket=False,
            )

        logger.debug(
            f"[{camera_id}] 跟踪更新: id={track_id}, event_id={event_id[:8] if event_id else ''}, "
            f"confidence={confidence:.3f}"
        )

    def _on_object_end(self, camera_id: str, obj_dict: dict, frame_time: float):
        """目标消失回调 - 移入 pending_end_events 等待宽限期（事件层合并去重）

        事件层合并策略：
        - 目标消失时不立即结束事件，移入 pending_end_events 等待宽限期
        - 宽限期内若有同 label+bbox 相近的新目标出现，认为是 ID switch，复用原 event_id
        - 宽限期超时仍无匹配，才真正结束事件（_finalize_event_end）

        特殊处理：
        - pending_required_zones 事件（目标从未进入必需区域）：直接丢弃，不进宽限期
        """
        # 诊断：统计 end 回调触发次数
        self._end_triggered_count = getattr(self, "_end_triggered_count", 0) + 1

        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return

        track_id = obj_dict.get("id", "")

        # 检查是否有活跃事件
        active_event = self.active_events.get(camera_id, {}).get(track_id)
        if active_event is None:
            return

        # 必需区域过滤：pending 事件（目标从未进入必需区域）直接丢弃，不产生任何事件
        # 不进宽限期（这类事件本就未真正创建）
        if active_event.get("pending_required_zones") and active_event.get("event_id") is None:
            del self.active_events[camera_id][track_id]
            logger.debug(
                f"[{camera_id}] 目标 {track_id} 未进入必需区域，丢弃事件"
            )
            # 如果消失的是当前 PTZ 跟踪目标，清除 PTZ 跟踪状态
            if self.ptz_tracked_object.get(camera_id) == track_id:
                self._clear_ptz_tracking(camera_id, frame_time)
            return

        event_id = active_event.get("event_id", "")
        class_name = obj_dict.get("label", "unknown")
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        # 获取结束快照（目标消失时的帧，复用主循环已读帧）
        # 宽限期后 _current_frame 已变，所以现在保存
        entered_zones = obj_dict.get("entered_zones", []) or []
        passes_zones = event_output.passes_required_zones(entered_zones)
        end_image = None
        if passes_zones and event_output.capture_save_full_on_end:
            end_image = self._current_frame
            end_image_aligned = self._current_frame_aligned
            if end_image is None:
                # 兜底读 shm，返回 (frame, aligned)
                end_image, end_image_aligned = self._read_frame_from_shm(camera_id)
            if end_image is not None:
                if not end_image_aligned:
                    logger.warning(
                        f"[{camera_id}] 事件结束快照帧未对齐(降级使用最新帧): "
                        f"event_id={event_id}, track_id={obj_dict.get('id')}, "
                        f"检测框可能滞后于画面"
                    )
                end_image = self._prepare_event_image(camera_id, end_image)

        # 移入 pending_end_events，等待宽限期
        # 宽限期内若有新目标匹配，复用 event_id 延续事件；超时则 _finalize_event_end
        if camera_id not in self.pending_end_events:
            self.pending_end_events[camera_id] = {}
        self.pending_end_events[camera_id][track_id] = {
            "event_id": event_id,
            "label": class_name,
            "bbox": bbox,
            "end_frame_time": frame_time,
            "end_wall_time": time.time(),
            "obj_dict": obj_dict,
            "active_event": active_event,
            "end_image": end_image,
        }

        # 从活跃事件中移除（track_id 已消失，不能再被 update 回调访问）
        del self.active_events[camera_id][track_id]

        # PTZ 跟踪目标消失：立即清除（PTZ 不能等待宽限期，否则会跟踪已消失的目标）
        if self.ptz_tracked_object.get(camera_id) == track_id:
            self._clear_ptz_tracking(camera_id, frame_time)

        logger.debug(
            f"[{camera_id}] 跟踪事件进入宽限期: id={track_id}, "
            f"event_id={event_id[:8] if event_id else ''}, label={class_name}, "
            f"grace_period={self.event_grace_period}s"
        )

    def _on_object_autotrack(
        self, camera_id: str, obj_dict: dict, frame_time: float
    ):
        """
        PTZ 自动跟踪回调

        当目标位置发生明显移动时触发,更新对应相机的 PTZMetrics 共享状态:
        - frame_time: 当前帧时间（供运动估计器判断 PTZ 移动期间）
        - tracking_active: 设置为活跃
        - 选择跟踪目标（若当前无跟踪目标,选择该目标作为跟踪对象）

        实际 PTZ ONVIF 控制由独立 PTZ 控制进程基于
        src/cameras/ptz_service.py 的 PTZController 读取 PTZMetrics 完成。

        Args:
            camera_id: 摄像机ID
            obj_dict: 目标字典（TrackedObject.to_dict() 输出）
            frame_time: 当前帧时间戳
        """
        metrics = self.ptz_metrics.get(camera_id)
        if metrics is None:
            return

        # 未启用自动跟踪则跳过
        if not metrics.is_autotracker_enabled():
            return

        track_id = obj_dict.get("id", "")

        # 选择跟踪目标:若当前无跟踪目标,则选当前目标
        current_tracked = self.ptz_tracked_object.get(camera_id)
        if current_tracked is None:
            self.ptz_tracked_object[camera_id] = track_id
            current_tracked = track_id
            logger.info(
                f"[{camera_id}] PTZ 锁定跟踪目标: id={track_id}, "
                f"label={obj_dict.get('label', 'unknown')}"
            )

        # 仅更新当前跟踪目标的状态
        if current_tracked != track_id:
            return

        # 更新 PTZMetrics 共享状态
        try:
            metrics.frame_time.value = frame_time
            metrics.tracking_active.set()
            metrics.motor_stopped.clear()
        except Exception as e:
            logger.debug(f"[{camera_id}] 更新 PTZMetrics 失败: {e}")

    def _clear_ptz_tracking(self, camera_id: str, frame_time: float):
        """
        清除 PTZ 跟踪状态

        当跟踪目标消失或停止跟踪时调用,通知 PTZ 控制进程停止移动。

        Args:
            camera_id: 摄像机ID
            frame_time: 当前帧时间戳
        """
        metrics = self.ptz_metrics.get(camera_id)
        if metrics is None:
            return

        try:
            metrics.stop_time.value = frame_time
            metrics.tracking_active.clear()
            metrics.motor_stopped.set()
        except Exception as e:
            logger.debug(f"[{camera_id}] 清除 PTZMetrics 失败: {e}")

        self.ptz_tracked_object[camera_id] = None
        logger.info(f"[{camera_id}] PTZ 跟踪已清除")

    def _read_frame_from_shm(self, camera_id: str, shm_info: Optional[dict] = None) -> Tuple[Optional[np.ndarray], bool]:
        """
        从共享内存回读帧

        使用传入的 shm_info（或 self._current_shm_info）回读 YUV 帧,转换为 RGB。
        同时传入 frame_seq 校验块是否已被环形缓冲区覆盖。

        Args:
            camera_id: 摄像机ID
            shm_info: 共享内存信息（线程安全：异步调用时传入拷贝，不依赖 self 状态）
                None 时使用 self._current_shm_info（向后兼容同步调用）

        Returns:
            (rgb_frame, aligned): rgb_frame 为 RGB 帧或 None；
                aligned 为 True 表示帧序号匹配（帧与检测框对齐），
                False 表示降级使用最新帧（帧与检测框可能错位，需记录告警）
        """
        if shm_info is None:
            shm_info = getattr(self, "_current_shm_info", None)
        if shm_info is None:
            return None, False

        shm_block_id = shm_info.get("shm_block_id")
        shm_name = shm_info.get("shm_name")
        frame_seq = shm_info.get("frame_seq")

        if shm_block_id is None or shm_name is None:
            return None, False

        # 获取帧形状
        config = self.camera_configs.get(camera_id, {})
        frame_shape = config.get("frame_shape", (640, 640))

        try:
            from src.utils.shared_memory import read_frame_from_shared_memory

            rgb_frame, aligned = read_frame_from_shared_memory(
                shm_name=shm_name,
                shm_block_id=shm_block_id,
                frame_shape=frame_shape,
                expected_frame_seq=frame_seq,
                return_aligned=True,
            )
            return rgb_frame, aligned
        except Exception as e:
            logger.debug(f"[{camera_id}] 回读帧失败: {e}")
            return None, False

    def _is_ai_stream_enabled(self, camera_id: str) -> bool:
        """检查摄像机是否启用 AI 流推送。

        优先使用文件标记方案（不依赖 Manager dict 代理的跨进程传递）：
        - flag_dir 配置存在时，检查 {flag_dir}/{camera_id}.flag 文件是否存在
        - 否则降级到 ai_stream_enabled_state（Manager dict 代理，向后兼容）

        Args:
            camera_id: 摄像机ID

        Returns:
            bool: 是否启用 AI 流推送
        """
        # 优先：文件标记方案
        if self.ai_stream_flag_dir:
            flag_file = os.path.join(self.ai_stream_flag_dir, f'{camera_id}.flag')
            return os.path.exists(flag_file)

        # 降级：Manager dict 代理（向后兼容，旧配置无 flag_dir 时使用）
        if self.ai_stream_enabled_state is not None:
            return bool(self.ai_stream_enabled_state.get(camera_id, False))

        return False

    def _async_push_frame_cache(
        self,
        camera_id: str,
        frame_cache,
        frame_time: float,
        frame: np.ndarray,
    ):
        """FrameCache 异步写入（JPEG 编码 ~2-5ms 移出主循环）

        流程：
        1. per-camera 背压锁：该 camera 上一帧还在编码中则丢弃本帧，避免积压
        2. submit 到 frame_cache_executor：JPEG 编码 + deque.append 全部异步
        3. 诊断计数在主循环外更新（push 返回值在 finally 中统计）

        说明：
        - FrameCache.push 内部已加锁保护 _last_sample_time 和 _frames，
          多线程并发调用安全
        - 背压丢弃是正常现象（cache_fps 采样率远低于视频帧率，大部分帧
          本就会被采样过滤，丢一两帧不影响 GIF 质量）
        """
        # per-camera 背压锁：上一帧编码中则丢弃
        camera_lock = self.frame_cache_inflight_locks.get(camera_id)
        if camera_lock is None:
            camera_lock = threading.Lock()
            self.frame_cache_inflight_locks[camera_id] = camera_lock
        if not camera_lock.acquire(blocking=False):
            # 背压丢弃，不计入 _frame_cache_push_total（未实际调用 push）
            return

        if not self.frame_cache_executor:
            camera_lock.release()
            return

        # submit：采样判断 + JPEG 编码 + append 全部在线程池异步执行
        # frame.copy() 防御性拷贝（主循环下一帧可能覆写 self._current_frame）
        self.frame_cache_executor.submit(
            self._async_push_frame_cache_task,
            camera_id,
            frame_cache,
            frame_time,
            frame.copy(),
            camera_lock,
        )

    def _async_push_frame_cache_task(
        self,
        camera_id: str,
        frame_cache,
        frame_time: float,
        frame: np.ndarray,
        camera_lock: threading.Lock,
    ):
        """线程池任务：调用 FrameCache.push（采样+JPEG编码+append），finally 释放背压锁"""
        try:
            self._frame_cache_push_total += 1
            if frame_cache.push(frame_time, frame):
                self._frame_cache_push_success += 1
        except Exception as e:
            logger.debug(f"[{camera_id}] FrameCache 异步写入异常: {e}")
        finally:
            try:
                camera_lock.release()
            except RuntimeError:
                pass

    # ========================================
    # 事件层合并：跟踪 ID switch 去重
    # ========================================

    @staticmethod
    def _bbox_iou(box_a: list, box_b: list) -> float:
        """计算两个 bbox 的 IoU

        Args:
            box_a, box_b: [x1, y1, x2, y2]

        Returns:
            IoU 值 [0, 1]
        """
        if not box_a or not box_b or len(box_a) < 4 or len(box_b) < 4:
            return 0.0
        x1 = max(box_a[0], box_b[0])
        y1 = max(box_a[1], box_b[1])
        x2 = min(box_a[2], box_b[2])
        y2 = min(box_a[3], box_b[3])
        inter_w = max(0, x2 - x1)
        inter_h = max(0, y2 - y1)
        inter = inter_w * inter_h
        area_a = max(0, box_a[2] - box_a[0]) * max(0, box_a[3] - box_a[1])
        area_b = max(0, box_b[2] - box_b[0]) * max(0, box_b[3] - box_b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _bbox_center_distance(box_a: list, box_b: list) -> float:
        """计算两个 bbox 中心点的欧氏距离

        Args:
            box_a, box_b: [x1, y1, x2, y2]

        Returns:
            中心点距离（像素）
        """
        if not box_a or not box_b or len(box_a) < 4 or len(box_b) < 4:
            return float('inf')
        cx_a = (box_a[0] + box_a[2]) / 2
        cy_a = (box_a[1] + box_a[3]) / 2
        cx_b = (box_b[0] + box_b[2]) / 2
        cy_b = (box_b[1] + box_b[3]) / 2
        return ((cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2) ** 0.5

    @staticmethod
    def _bbox_avg_size(box: list) -> float:
        """计算 bbox 的平均边长（用于中心点距离归一化）

        Args:
            box: [x1, y1, x2, y2]

        Returns:
            平均边长（像素）
        """
        if not box or len(box) < 4:
            return 1.0
        w = max(0, box[2] - box[0])
        h = max(0, box[3] - box[1])
        return (w + h) / 2 if (w + h) > 0 else 1.0

    def _match_pending_end_event(self, camera_id: str, label: str, bbox: list) -> Optional[str]:
        """在 pending_end_events 中查找与新目标匹配的事件（用于 ID switch 合并）

        匹配条件（满足任一）：
        1. 同 label 且 bbox IoU > 0.3
        2. 同 label 且中心点距离 < 平均尺寸 × 0.5（目标可能移动了一段距离）

        Args:
            camera_id: 摄像机ID
            label: 新目标的类别标签
            bbox: 新目标的 bbox [x1, y1, x2, y2]

        Returns:
            匹配的 pending track_id，无匹配返回 None
        """
        cam_pending = self.pending_end_events.get(camera_id, {})
        if not cam_pending:
            return None

        best_match = None
        best_score = 0.0

        for pending_track_id, pending_info in cam_pending.items():
            # 同 label 才匹配
            if pending_info.get("label") != label:
                continue

            pending_bbox = pending_info.get("bbox", [])
            if not pending_bbox:
                continue

            # 条件1：IoU > 0.3
            iou = self._bbox_iou(bbox, pending_bbox)
            if iou > 0.3 and iou > best_score:
                best_score = iou
                best_match = pending_track_id
                continue

            # 条件2：中心点距离 < 平均尺寸 × 0.5
            dist = self._bbox_center_distance(bbox, pending_bbox)
            avg_size = (self._bbox_avg_size(bbox) + self._bbox_avg_size(pending_bbox)) / 2
            if dist < avg_size * 0.5:
                # 用距离归一化后的得分（越小越好，转为 0-1 越大越好）
                score = 1.0 - min(1.0, dist / (avg_size * 0.5))
                if score > best_score:
                    best_score = score
                    best_match = pending_track_id

        return best_match

    def _cleanup_expired_pending_ends(self, current_frame_time: float):
        """清理超宽限期的 pending_end 事件，真正结束它们

        在主循环每次迭代调用，检查所有 camera 的 pending_end_events，
        将超过 event_grace_period 的事件真正结束。

        Args:
            current_frame_time: 当前帧的视频相对时间（用于宽限期判断）
        """
        for camera_id in list(self.pending_end_events.keys()):
            cam_pending = self.pending_end_events[camera_id]
            if not cam_pending:
                continue

            for pending_track_id in list(cam_pending.keys()):
                pending_info = cam_pending[pending_track_id]
                end_frame_time = pending_info.get("end_frame_time", 0)
                elapsed = current_frame_time - end_frame_time

                if elapsed >= self.event_grace_period:
                    # 超过宽限期，真正结束事件
                    self._finalize_event_end(camera_id, pending_track_id, pending_info)

    def _finalize_event_end(self, camera_id: str, pending_track_id: str, pending_info: dict):
        """真正结束一个事件（从 pending_end 移出，执行原 _on_object_end 的结束逻辑）

        Args:
            camera_id: 摄像机ID
            pending_track_id: pending_end_events 中的 key（原 track_id）
            pending_info: pending_end_events 中保存的事件信息
        """
        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            self.pending_end_events.get(camera_id, {}).pop(pending_track_id, None)
            return

        obj_dict = pending_info.get("obj_dict", {})
        active_event = pending_info.get("active_event", {})
        frame_time = pending_info.get("end_frame_time", 0)

        event_id = active_event.get("event_id", "")
        event_start_time = active_event.get("start_wall_time", time.time())
        event_start_frame_time = active_event.get("start_time", frame_time)

        # 诊断：记录事件持续时间
        self._event_durations = getattr(self, "_event_durations", [])
        self._event_durations.append(frame_time - event_start_frame_time)
        if len(self._event_durations) > 100:
            self._event_durations = self._event_durations[-100:]

        # 构建更新数据
        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        update_data = {
            "confidence": confidence,
            "bbox": bbox,
            "class_name": class_name,
            "end_time": time.time(),
            "start_time": event_start_time,
            "current_zones": obj_dict.get("current_zones", []),
            "entered_zones": obj_dict.get("entered_zones", []),
            "pending_loitering": obj_dict.get("pending_loitering", []),
            "loitered_zones": obj_dict.get("loitered_zones", []),
        }

        # required_zones 过滤
        entered_zones = obj_dict.get("entered_zones", []) or []
        passes_zones = event_output.passes_required_zones(entered_zones)

        # 获取置信度最高的缩略图
        best_thumbnail = event_output.get_best_thumbnail(event_id)
        best_image = None
        if passes_zones and best_thumbnail and best_thumbnail.get("image") is not None:
            best_image = best_thumbnail["image"]

        # 结束快照：使用 pending 时保存的 end_image（目标消失时的帧）
        end_image = pending_info.get("end_image")

        final_image = best_image if best_image is not None else end_image

        if passes_zones:
            source = (
                "best_image" if best_image is not None
                else "end_image" if end_image is not None
                else "none"
            )
            logger.debug(
                f"[{camera_id}] 事件结束(宽限期超时): event_id={event_id[:8] if event_id else ''}, "
                f"label={class_name}, final_image来源={source}, "
                f"duration={frame_time - event_start_frame_time:.2f}s"
            )

        if not passes_zones:
            # 未通过区域过滤：保持原行为，仅更新基础数据（状态=已结束，不参与去重）
            event_output.update_event(
                event_id,
                status="tracking_end",
                data=update_data,
                image=final_image,
                push_websocket=True,
                event_type=self._derive_event_type(obj_dict),
            )
            # MQTT：仅推最终事件——tracking_end 为终态且未被去重，推送完整数据
            try:
                event_output.push_final_to_mqtt(event_id)
            except Exception as e:
                logger.debug(f"[MQTT] 推送 tracking_end 事件失败 event={event_id[:8]}: {e}")
        else:
            # ========================================
            # 通过区域过滤：新状态机流转
            # 1. 规则去重前置（事件管理功能，独立于大模型，不消耗 token）
            #    - 判重 → 状态=被去重，不生成最终图片/GIF，跳过语义去重（节省资源）
            #    - 未判重 → 生成图片和事件数据 → 状态=初始事件
            # 2. 语义去重（大模型，前提打开大模型功能）→ 最终事件 / 被去重
            # ========================================
            dedup_status = "normal"
            dedup_reason = ""
            if self.rule_deduper is not None:
                try:
                    dedup_status, dedup_reason = self.rule_deduper.check_duplicate(
                        event_id=event_id,
                        camera_id=camera_id,
                        class_name=class_name,
                        bbox=bbox,
                    )
                except Exception as e:
                    logger.warning(f"[RuleDedup] 规则去重失败 event={event_id[:8]}: {e}")

            if dedup_status == "duplicate":
                # 规则去重判重：状态=被去重，不生成最终图片/GIF，跳过语义去重
                event_output.update_event(
                    event_id,
                    status="duplicate",
                    data=update_data,
                    image=None,
                    push_websocket=True,
                    event_type=self._derive_event_type(obj_dict),
                )
                from src.tasks.task_manager import TaskManager
                tm = TaskManager()
                tm.update_event(
                    event_id,
                    dedup_status=dedup_status,
                    dedup_reason=dedup_reason,
                )
                logger.info(
                    f"[{camera_id}] 事件被规则去重: event_id={event_id[:8]}, "
                    f"reason={dedup_reason[:80]}"
                )
            else:
                # 规则未判重：生成图片和事件数据 → 状态=初始事件
                event_output.update_event(
                    event_id,
                    status="initial",
                    data=update_data,
                    image=final_image,
                    push_websocket=True,
                    event_type=self._derive_event_type(obj_dict),
                )

                # 异步生成 GIF（仅初始事件）
                if event_output.gif_enabled and self.gif_executor:
                    frame_cache = self.frame_caches.get(camera_id)
                    if frame_cache is not None:
                        gif_cfg = event_output.gif_cfg
                        pre_seconds = gif_cfg.get('pre_seconds', 5.0)
                        post_seconds = gif_cfg.get('post_seconds', 0.0)
                        max_frames = gif_cfg.get('max_frames', 60)

                        retrieval_start = event_start_frame_time - pre_seconds
                        retrieval_end = frame_time

                        cached_frames = frame_cache.get_frames(
                            retrieval_start, retrieval_end, max_frames=max_frames
                        )

                        if cached_frames and len(cached_frames) >= 2:
                            frames = [img for _, img in cached_frames]
                            trajectory_points = obj_dict.get("trajectory", [])

                            logger.debug(
                                f"[{camera_id}] GIF 帧回取成功(宽限期超时): {len(frames)}帧"
                            )

                            if post_seconds > 0:
                                self._schedule_delayed_gif(
                                    event_output, event_id, camera_id,
                                    frames, bbox, trajectory_points,
                                    frame_time, post_seconds, max_frames
                                )
                            else:
                                self.gif_executor.submit(
                                    self._async_generate_gif,
                                    event_output, event_id,
                                    frames=frames, camera_id=camera_id,
                                    bbox=bbox, trajectory_points=trajectory_points,
                                )

                # 语义去重（大模型，前提打开大模型功能）+ 描述：
                # 异步完成后由 _async_llm_process 切换状态 → 最终事件 / 被去重
                if final_image is not None and self.llm_executor:
                    cam_cfg = self.camera_configs.get(camera_id, {})
                    event_info = {
                        'class_name': class_name,
                        'camera_name': cam_cfg.get('camera_name', camera_id),
                        'zone_name': ",".join(entered_zones) if entered_zones else '',
                        'event_type': self._derive_event_type(obj_dict),
                        'duration': frame_time - event_start_frame_time,
                        'start_time': event_start_time,
                    }
                    self.llm_executor.submit(
                        self._async_llm_process,
                        event_id=event_id,
                        camera_id=camera_id,
                        class_name=class_name,
                        bbox=bbox,
                        image=final_image,
                        event_info=event_info,
                    )
                else:
                    # 大模型未启用或无最终快照：直接作为最终事件（状态切换）
                    from src.tasks.task_manager import TaskManager
                    tm = TaskManager()
                    tm.update_event(event_id, status="final", dedup_status="normal")
                    # 推送状态切换到前端（驾驶舱实时刷新徽章），与 _async_llm_process 保持一致
                    try:
                        from src.system.system_config_loader import get_web_server_host, get_web_server_port
                        payload = {
                            'event_id': event_id,
                            'status': 'final',
                            'dedup_status': 'normal',
                            'data': {},
                        }
                        if self._http_session is not None:
                            self._http_session.post(
                                f'http://{get_web_server_host("0.0.0.0")}:{get_web_server_port()}/api/events/update',
                                json=payload,
                                timeout=2.0,
                            )
                        else:
                            import requests
                            requests.post(
                                f'http://{get_web_server_host("0.0.0.0")}:{get_web_server_port()}/api/events/update',
                                json=payload,
                                timeout=2.0,
                            )
                    except Exception as e:
                        logger.debug(f"[Event] 推送最终状态失败 event={event_id[:8]}: {e}")
                    # MQTT：仅推最终事件——规则去重通过且无需语义去重，立即推送
                    try:
                        event_output.push_final_to_mqtt(event_id)
                    except Exception as e:
                        logger.debug(f"[MQTT] 推送最终事件失败 event={event_id[:8]}: {e}")

        # 清理缩略图
        event_output.clear_thumbnail(event_id)

        # 从 pending_end_events 移除
        self.pending_end_events.get(camera_id, {}).pop(pending_track_id, None)

        # PTZ 跟踪清理
        if self.ptz_tracked_object.get(camera_id) == pending_track_id:
            self._clear_ptz_tracking(camera_id, frame_time)

        logger.info(
            f"[{camera_id}] 跟踪事件结束(宽限期超时): track_id={pending_track_id}, "
            f"event_id={event_id[:8] if event_id else ''}, label={class_name}, "
            f"duration={frame_time - event_start_frame_time:.1f}s"
        )

    def _async_llm_process(
        self,
        event_id: str,
        camera_id: str,
        class_name: str,
        bbox: list,
        image,
        event_info: dict,
    ):
        """大模型事件处理（在线程池中异步执行，不阻塞事件管道）

        规则去重已由事件管理独立执行，此处仅执行大模型功能：
        1. 若启用语义去重：大模型对比快照判断是否重复
           - 重复事件：标记 dedup_status=duplicate，不生成描述
           - 正常事件：继续生成描述
        2. 若启用描述且事件非重复：生成自然语言描述存入事件记录

        所有异常静默处理，绝不影响事件管道主流程。
        """
        # 编码图片为 JPEG bytes
        image_bytes = None
        try:
            from src.utils.image import encode_jpeg
            image_bytes = encode_jpeg(image, quality=85)
        except Exception as e:
            logger.debug(f"[LLM] 图片编码失败 event={event_id[:8]}: {e}")

        if image_bytes is None:
            # 无可处理快照：无法执行语义去重，直接作为最终事件
            from src.tasks.task_manager import TaskManager
            tm = TaskManager()
            tm.update_event(event_id, status="final", dedup_status="normal")
            # MQTT：仅推最终事件
            event_output = self.event_outputs.get(camera_id)
            if event_output is not None:
                try:
                    event_output.push_final_to_mqtt(event_id)
                except Exception as e:
                    logger.debug(f"[MQTT] 推送最终事件失败 event={event_id[:8]}: {e}")
            return

        # 语义层去重（大模型，仅对规则层未判重的候选事件）
        dedup_status = "normal"
        dedup_reason = ""
        from src.tasks.task_manager import TaskManager
        tm = TaskManager()
        if self.llm_deduplicator is not None:
            try:
                dedup_status, dedup_reason = self.llm_deduplicator.semantic_check(
                    event_id=event_id,
                    camera_id=camera_id,
                    class_name=class_name,
                    image_bytes=image_bytes,
                )
            except Exception as e:
                logger.warning(f"[LLM] 语义去重失败 event={event_id[:8]}: {e}")

        # 状态切换：重复 → 被去重；正常/未启用语义去重 → 最终事件
        # 写库后事件状态机完成最终流转（初始事件 → 最终事件 / 被去重）
        final_status = "duplicate" if dedup_status == "duplicate" else "final"
        tm.update_event(
            event_id,
            status=final_status,
            dedup_status=dedup_status,
            dedup_reason=dedup_reason,
        )

        # MQTT：仅推最终事件——被去重事件不推送
        if final_status == "final":
            event_output = self.event_outputs.get(camera_id)
            if event_output is not None:
                try:
                    event_output.push_final_to_mqtt(event_id)
                except Exception as e:
                    logger.debug(f"[MQTT] 推送最终事件失败 event={event_id[:8]}: {e}")

        # 推送状态切换到前端（驾驶舱实时刷新徽章）
        try:
            from src.system.system_config_loader import get_web_server_host, get_web_server_port
            payload = {
                'event_id': event_id,
                'status': final_status,
                'dedup_status': dedup_status,
                'dedup_reason': dedup_reason,
                'data': {},
            }
            if self._http_session is not None:
                self._http_session.post(
                    f'http://{get_web_server_host("0.0.0.0")}:{get_web_server_port()}/api/events/update',
                    json=payload,
                    timeout=2.0,
                )
            else:
                import requests
                requests.post(
                    f'http://{get_web_server_host("0.0.0.0")}:{get_web_server_port()}/api/events/update',
                    json=payload,
                    timeout=2.0,
                )
        except Exception as e:
            logger.debug(f"[LLM] 推送状态切换失败 event={event_id[:8]}: {e}")

        # 重复事件不再生成描述
        if dedup_status == "duplicate":
            logger.info(
                f"[LLM] 事件被判定为重复，跳过描述: "
                f"event={event_id[:8]}, reason={dedup_reason[:60]}"
            )
            return

        # 第二阶段：生成事件描述
        if self.llm_describer is not None:
            try:
                description = self.llm_describer.describe(
                    image_bytes=image_bytes,
                    event_info=event_info,
                )
                if description:
                    from src.tasks.task_manager import TaskManager
                    from datetime import datetime
                    tm = TaskManager()
                    tm.update_event(
                        event_id,
                        ai_description=description,
                        ai_description_model=self.llm_describer.config.model,
                        ai_description_time=datetime.now(),
                    )
                    logger.info(
                        f"[LLM] 事件描述已写入: event={event_id[:8]}, "
                        f"desc={description[:60]}"
                    )

                    # ✅ AI 描述写入后补推一次 MQTT（第三方订阅可获取描述）
                    event_output = self.event_outputs.get(camera_id)
                    if event_output is not None:
                        try:
                            event_output.push_ai_description_to_mqtt(event_id)
                        except Exception as e:
                            logger.debug(
                                f"[LLM] MQTT 补推 AI 描述失败 event={event_id[:8]}: {e}"
                            )
            except Exception as e:
                logger.warning(f"[LLM] 描述生成失败 event={event_id[:8]}: {e}")

    def _maybe_push_ai_stream(
        self, camera_id: str, frame_time: float, tracked_objects: dict,
        motion_boxes: list = None, regions: list = None,
        detection_results: list = None,
        frame: Optional[np.ndarray] = None,
    ):
        """AI 视频流推送（异步方案：主循环只做检查+计数+submit，画框+编码+HTTP在线程池）

        流程：
        1. 检查该摄像机是否启用 AI 流（文件标记优先，Manager dict 降级兼容）
        2. 帧计数 + interval 过滤
        3. 复用主循环已读帧（None 时兜底读 shm）
        4. per-camera 背压锁：该 camera 有帧在编码中则丢帧，避免积压
        5. submit 到线程池：画框+JPEG编码+HTTP推送全部异步
        """
        # 检查该摄像机是否启用 AI 流
        if not self._is_ai_stream_enabled(camera_id):
            # 每隔100帧打印一次日志，避免刷屏
            if not hasattr(self, '_ai_stream_check_counters'):
                self._ai_stream_check_counters = {}
            self._ai_stream_check_counters[camera_id] = self._ai_stream_check_counters.get(camera_id, 0) + 1
            if self._ai_stream_check_counters[camera_id] % 100 == 0:
                logger.debug(
                    f"[{camera_id}] AI流：未启用 (检查计数: {self._ai_stream_check_counters[camera_id]})"
                )
            return

        interval = self.ai_stream_config.get("interval", 10)
        self.ai_stream_frame_counters[camera_id] = (
            self.ai_stream_frame_counters.get(camera_id, 0) + 1
        )
        frame_seq = self.ai_stream_frame_counters[camera_id]

        if frame_seq % interval != 0:
            return

        # 复用主循环已读帧，None 时兜底读 shm
        frame_aligned = True
        if frame is None:
            # 兜底读 shm，返回 (frame, aligned)
            frame, frame_aligned = self._read_frame_from_shm(camera_id)
        if frame is None:
            # 读取失败，属正常背压情况，降为 DEBUG 避免刷屏
            logger.debug(f"[{camera_id}] AI流：回读帧失败，跳过推送 (frame_seq={frame_seq})")
            return
        if not frame_aligned:
            logger.warning(
                f"[{camera_id}] AI流帧未对齐(降级使用最新帧): "
                f"frame_seq={frame_seq}, 检测框可能滞后于画面"
            )

        # per-camera 背压锁：该 camera 上一帧还在编码中则丢弃本帧，避免积压
        camera_lock = self.ai_stream_inflight_locks.get(camera_id)
        if camera_lock is None:
            camera_lock = threading.Lock()
            self.ai_stream_inflight_locks[camera_id] = camera_lock
        if not camera_lock.acquire(blocking=False):
            logger.debug(f"[{camera_id}] AI流：背压丢弃 frame_seq={frame_seq}")
            return

        # 准备线程池任务参数
        api_url = self.ai_stream_config.get("api_url")
        cam_config = self.camera_configs.get(camera_id, {})
        task_id = cam_config.get("task_id", "")

        if not api_url or not self.ai_stream_executor:
            camera_lock.release()
            logger.warning(
                f"[{camera_id}] AI流：未推送 api_url={bool(api_url)} "
                f"executor={bool(self.ai_stream_executor)}"
            )
            return

        logger.debug(f"[{camera_id}] AI流：提交编码推送任务 frame_seq={frame_seq}")
        # submit：画框+编码+HTTP 全部在线程池异步执行
        # shallow copy 可变结构，避免主循环下一帧修改
        self.ai_stream_executor.submit(
            self._async_encode_and_push,
            frame,
            camera_id,
            frame_seq,
            dict(tracked_objects) if tracked_objects else {},
            list(motion_boxes) if motion_boxes else [],
            list(regions) if regions else [],
            list(detection_results) if detection_results else [],
            task_id,
            api_url,
            camera_lock,
        )

    def _async_encode_and_push(
        self,
        frame: np.ndarray,
        camera_id: str,
        frame_seq: int,
        tracked_objects: dict,
        motion_boxes: list,
        regions: list,
        detection_results: list,
        task_id: str,
        api_url: str,
        camera_lock: threading.Lock,
    ):
        """线程池任务：画框 + resize + JPEG编码 + base64 + HTTP推送（finally 释放背压锁）"""
        try:
            # 防御性 copy（主循环可能已覆写 self._current_frame，本任务持有的是参数引用，
            # 理论上安全；此 copy 防御未来 _draw_detections_on_frame 变更不再 copy 的风险）
            frame = frame.copy()

            # 1. 画框（PIL 中文渲染耗时主要在此，受 display_config 开关管控）
            frame_with_boxes = self._draw_detections_on_frame(
                frame, camera_id, tracked_objects, motion_boxes, regions, detection_results
            )

            # 2. 按原始宽高比缩放（降低带宽，最大宽度640）
            h, w = frame_with_boxes.shape[:2]
            if w > 640:
                scale = 640 / w
                frame_with_boxes = cv2.resize(frame_with_boxes, (640, int(h * scale)))

            # 3. JPEG编码（质量70）+ base64
            #    帧为 RGB 格式，使用 PIL 编码避免 cv2 的 BGR 假设，无需通道转换
            from src.utils.image import encode_jpeg
            jpeg_bytes = encode_jpeg(frame_with_boxes, quality=70)
            frame_base64 = base64.b64encode(jpeg_bytes).decode('utf-8')

            # 4. HTTP 推送（服务端已改为入队即返回，正常 < 50ms；
            #    timeout=2.0 仅防御偶发调度抖动/GIL 竞争，单帧超时可接受）
            # ✅ 使用复用的 Session（避免每次 requests.post 创建新 PoolManager 导致内存泄漏）
            if self._http_session is not None:
                response = self._http_session.post(
                    api_url,
                    json={
                        "task_id": task_id,
                        "camera_id": camera_id,
                        "frame_seq": frame_seq,
                        "frame_base64": frame_base64,
                    },
                    timeout=2.0,
                )
            else:
                # fallback：Session 不可用时退回直接调用
                import requests
                response = requests.post(
                    api_url,
                    json={
                        "task_id": task_id,
                        "camera_id": camera_id,
                        "frame_seq": frame_seq,
                        "frame_base64": frame_base64,
                    },
                    timeout=2.0,
                )
            if response.status_code == 200:
                logger.debug(f"[{camera_id}] AI帧推送成功: frame_seq={frame_seq}")
            else:
                logger.warning(
                    f"[{camera_id}] AI帧推送失败: status={response.status_code} "
                    f"frame_seq={frame_seq}"
                )
        except Exception as e:
            logger.error(f"[{camera_id}] AI帧编码推送异常: {e} frame_seq={frame_seq}")
        finally:
            # 释放 per-camera 背压锁（允许下一帧 submit）
            try:
                camera_lock.release()
            except RuntimeError:
                pass

    def _draw_detections_on_frame(self, frame: np.ndarray, camera_id: str,
                                    tracked_objects: dict, motion_boxes: list = None,
                                    regions: list = None,
                                    detection_results: list = None) -> np.ndarray:
        """
        在帧上绘制区域框、检测框、跟踪框和运动区域（受 display_config 开关管控）

        绘制逻辑：
        - 区域框（彩色半透明）：来自通道的 zones_config，受 show_zones 管控
        - 检测框（绿色）：来自 detection_results（所有被检测到的目标），受 show_detection_boxes 管控
        - 跟踪框（蓝色）：来自 tracked_objects（已进入跟踪的目标），受 show_track_boxes 管控
        - 轨迹（黄色）：来自 tracked_objects 的轨迹历史，受 show_trajectories 管控
        - 运动区域（青色）：来自 motion_boxes，受 show_motion_regions 管控

        Args:
            frame: RGB帧 (height, width, 3)
            camera_id: 摄像机ID（用于读取通道的 zones_config）
            tracked_objects: 跟踪对象字典 {obj_id: {box, label, score, ...}}
            motion_boxes: 运动检测框列表
            regions: 检测区域列表
            detection_results: 原始检测结果列表 [{label, score, box}, ...]

        Returns:
            绘制后的帧
        """
        import cv2

        # 中文文字绘制支持（PIL 渲染，失败时退回 cv2.putText）
        try:
            from src.utils.font_utils import draw_utf8_label_on_rgb
            _has_chinese_font = True
        except ImportError:
            _has_chinese_font = False

        cfg = self.display_config
        display_cfg = cfg.get('display', {})
        det_box_cfg = cfg.get('detection_box', {})
        track_box_cfg = cfg.get('track_box', {})
        trajectory_cfg = cfg.get('trajectory', {})
        motion_cfg = cfg.get('motion_region', {})

        # 帧为 RGB 格式（来自 YUV→RGB 转换），display_config 中颜色也为 RGB 格式
        # 直接使用配置颜色，无需通道翻转
        show_zones = display_cfg.get('show_zones', True)
        show_detection = display_cfg.get('show_detection_boxes', True)
        show_track = display_cfg.get('show_track_boxes', True)
        show_trajectories = display_cfg.get('show_trajectories', False)
        show_motion = display_cfg.get('show_motion_regions', True)
        trajectory_length = display_cfg.get('trajectory_history_length', 30)

        frame_copy = frame.copy()

        # ========================================
        # 0. 绘制区域框（受 show_zones 管控）
        #    来自通道的 zones_config（归一化坐标 [0-1]）
        # ========================================
        if show_zones:
            cam_config = self.camera_configs.get(camera_id, {})
            zones_config = cam_config.get('zones_config', [])
            if zones_config:
                frame_h, frame_w = frame_copy.shape[:2]
                for zone_cfg in zones_config:
                    polygon_raw = zone_cfg.get('polygon', [])
                    if not polygon_raw or len(polygon_raw) < 3:
                        continue
                    # 根据 polygon_type 决定坐标转换方式
                    polygon_type = zone_cfg.get('polygon_type', 'normalized')
                    if polygon_type == 'normalized':
                        # 归一化坐标 [0-1] 转换为帧像素坐标
                        pts = np.array([
                            [int(p[0] * frame_w), int(p[1] * frame_h)]
                            for p in polygon_raw
                        ], dtype=np.int32)
                    else:
                        # 像素坐标直接使用
                        pts = np.array([
                            [int(p[0]), int(p[1])]
                            for p in polygon_raw
                        ], dtype=np.int32)
                    # 颜色（zones_config 中为 RGB 格式，帧也是 RGB，直接使用）
                    zone_color = tuple(zone_cfg.get('color', [255, 0, 0]))
                    # 半透明填充
                    overlay = frame_copy.copy()
                    cv2.fillPoly(overlay, [pts], zone_color)
                    frame_copy = cv2.addWeighted(overlay, 0.2, frame_copy, 0.8, 0)
                    # 边框
                    cv2.polylines(frame_copy, [pts], True, zone_color, 2)
                    # 区域名称（支持中文，绘制在区域重心位置）
                    zone_name = zone_cfg.get('name', '')
                    if zone_name:
                        M = cv2.moments(pts)
                        if M["m00"] != 0:
                            cX = int(M["m10"] / M["m00"])
                            cY = int(M["m01"] / M["m00"])
                            if _has_chinese_font:
                                draw_utf8_label_on_rgb(frame_copy, zone_name, (cX - 20, cY - 7),
                                                       font_size=14, text_color_rgb=(255, 255, 255))
                            else:
                                cv2.putText(frame_copy, zone_name, (cX - 20, cY),
                                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # ========================================
        # 1. 绘制运动区域（受 show_motion_regions 管控）
        # ========================================
        if show_motion and motion_boxes:
            motion_color = tuple(motion_cfg.get('color', [0, 255, 255]))
            motion_thickness = motion_cfg.get('thickness', 1)
            fill_alpha = motion_cfg.get('fill_alpha', 50)
            fill_color = tuple(motion_cfg.get('fill_color', [0, 255, 255]))

            for mbox in motion_boxes:
                if len(mbox) >= 4:
                    mx1, my1, mx2, my2 = [int(v) for v in mbox[:4]]
                    cv2.rectangle(frame_copy, (mx1, my1), (mx2, my2), motion_color, motion_thickness)
                    if fill_alpha > 0:
                        overlay = frame_copy.copy()
                        cv2.rectangle(overlay, (mx1, my1), (mx2, my2), fill_color, -1)
                        alpha = fill_alpha / 255.0
                        frame_copy = cv2.addWeighted(overlay, alpha, frame_copy, 1 - alpha, 0)

        # ========================================
        # 2. 绘制检测框（受 show_detection_boxes 管控）
        #    来自 detection_results：所有被检测到的目标（含未进入跟踪的）
        # ========================================
        if show_detection and detection_results:
            det_color = tuple(det_box_cfg.get('color', [0, 255, 0]))
            det_thickness = det_box_cfg.get('thickness', 2)
            det_font_scale = det_box_cfg.get('font_scale', 0.5)
            det_label_color = tuple(det_box_cfg.get('label_color', [0, 255, 0]))

            for det in detection_results:
                bbox = det.get('box', [0, 0, 0, 0])
                label = det.get('label', 'unknown')
                score = det.get('score', 0.0)
                x1, y1, x2, y2 = [int(v) for v in bbox]
                cv2.rectangle(frame_copy, (x1, y1), (x2, y2), det_color, det_thickness)
                text = f"{label}: {score:.2f}"
                # 检测框标签（支持中文）
                label_y = max(y1 - 20, 2)
                if _has_chinese_font:
                    # det_label_color 为 RGB，可直接传给 draw_utf8_label_on_rgb
                    draw_utf8_label_on_rgb(frame_copy, text, (x1, label_y),
                                           font_size=14, text_color_rgb=det_label_color)
                else:
                    cv2.putText(frame_copy, text, (x1, y1 - 10),
                               cv2.FONT_HERSHEY_SIMPLEX, det_font_scale, det_label_color, 1)

        # ========================================
        # 3. 绘制跟踪框（受 show_track_boxes 管控）
        #    来自 tracked_objects：已进入跟踪的目标（持续≥min_initialized帧）
        # ========================================
        if show_track and tracked_objects:
            track_color = tuple(track_box_cfg.get('color', [0, 0, 255]))
            track_thickness = track_box_cfg.get('thickness', 2)
            track_font_scale = track_box_cfg.get('font_scale', 0.5)
            track_label_color = tuple(track_box_cfg.get('label_color', [0, 0, 255]))
            # 徘徊告警高亮配置（达到 loitering 阈值的目标边框变红+闪烁）
            loitering_color = tuple(track_box_cfg.get('loitering_color', [255, 0, 0]))
            loitering_thickness = track_box_cfg.get('loitering_thickness', 3)
            loitering_blink = track_box_cfg.get('loitering_blink', True)
            # 闪烁：基于系统时间 0.5 秒交替，所有目标同步明灭
            blink_on = (int(time.time() * 2) % 2 == 0)

            for obj_id, obj_dict in tracked_objects.items():
                bbox = obj_dict.get("box", [0, 0, 0, 0])
                x1, y1, x2, y2 = [int(v) for v in bbox]

                # zone 状态：当前所在区域 + 是否达到徘徊阈值
                current_zones = obj_dict.get("current_zones", []) or []
                pending_loitering = obj_dict.get("pending_loitering", []) or []
                is_loitering = len(pending_loitering) > 0

                # 边框颜色/线宽：徘徊目标用红色高亮（闪烁时奇数帧退回正常框，形成明灭效果）
                if is_loitering and (not loitering_blink or blink_on):
                    box_color = loitering_color
                    box_thickness = loitering_thickness
                else:
                    box_color = track_color
                    box_thickness = track_thickness
                cv2.rectangle(frame_copy, (x1, y1), (x2, y2), box_color, box_thickness)

                # 标签：ID + 当前区域 + 徘徊标记（例：ID:a1b2c3d4 | Z1/Z2 | 徘徊）
                parts = [f"ID: {obj_id[:8]}"]
                if current_zones:
                    parts.append("/".join(current_zones))
                if is_loitering:
                    parts.append("徘徊")
                track_text = " | ".join(parts)
                # 跟踪框标签（支持中文）
                if _has_chinese_font:
                    # track_label_color 为 RGB，可直接传给 draw_utf8_label_on_rgb
                    draw_utf8_label_on_rgb(frame_copy, track_text, (x1, y2 + 5),
                                           font_size=14, text_color_rgb=track_label_color)
                else:
                    cv2.putText(frame_copy, track_text, (x1, y2 + 20),
                               cv2.FONT_HERSHEY_SIMPLEX, track_font_scale, track_label_color, 1)

        # ========================================
        # 4. 绘制轨迹（受 show_trajectories 管控）
        #    来自 tracked_objects 的轨迹历史
        # ========================================
        if show_trajectories and tracked_objects:
            traj_color = tuple(trajectory_cfg.get('color', [255, 255, 0]))
            traj_thickness = trajectory_cfg.get('thickness', 1)
            point_size = trajectory_cfg.get('point_size', 2)
            point_color = tuple(trajectory_cfg.get('point_color', [255, 0, 0]))

            for obj_id, obj_dict in tracked_objects.items():
                trajectory = obj_dict.get("trajectory", [])
                if trajectory:
                    recent_traj = trajectory[-trajectory_length:]
                    points = [(int(p[0]), int(p[1])) for p in recent_traj if len(p) >= 2]
                    if len(points) >= 2:
                        cv2.polylines(frame_copy, [np.array(points)], False, traj_color, traj_thickness)
                    for pt in points:
                        cv2.circle(frame_copy, pt, point_size, point_color, -1)

        return frame_copy

    def _prepare_event_image(self, camera_id: str, image: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """在事件图片上绘制检测框（与 AI 视频流保持一致）

        事件图片原本使用原始帧（无检测框），导致前端展示的事件缩略图缺少检测框。
        本方法复用 AI 视频流的 _draw_detections_on_frame 绘制逻辑，保证事件图片
        与 AI 视频流的视觉效果一致（检测框、跟踪框、轨迹、区域框等）。

        Args:
            camera_id: 摄像机ID
            image: 原始 RGB 帧

        Returns:
            绘制检测框后的帧；输入为 None 时返回 None
        """
        if image is None:
            return None

        tracked_objects = getattr(self, '_current_tracked_objects', {}) or {}
        regions = getattr(self, '_current_regions', []) or []
        detection_results = getattr(self, '_current_detection_results', []) or []

        # 事件图片不绘制运动框（motion_boxes=None），只保留检测框/跟踪框/区域框
        return self._draw_detections_on_frame(
            image, camera_id, tracked_objects, None, regions, detection_results
        )

    def _schedule_delayed_gif(self, event_output, event_id: str, camera_id: str,
                              pre_frames: list, bbox: tuple, trajectory_points: list,
                              end_time: float, post_seconds: float, max_frames: int):
        """
        延迟生成 GIF（等待 post_seconds 后补充消失后帧再生成）

        使用 threading.Timer 在独立线程中等待 post_seconds（不占用 gif_executor worker），
        然后从 FrameCache 补充 post 阶段帧，合并后提交 GIF 生成任务。

        ⚠️ 内存注意：pre_frames 在 Timer 等待期间被闭包持有。若事件极频繁且 post_seconds 较大，
        可考虑先将 pre_frames 压缩为 JPEG 字节再持有（当前未做此优化，因事件频率一般可控）。

        Args:
            event_output: EventOutput 实例
            event_id: 事件 ID
            camera_id: 摄像机 ID
            pre_frames: 事件开始到消失的帧序列
            bbox: 检测框
            trajectory_points: 轨迹点
            end_time: 事件结束时间
            post_seconds: 事件结束后等待秒数
            max_frames: 最大帧数
        """
        def _delayed_task():
            """Timer 回调：补充 post 帧后提交 GIF 生成"""
            try:
                # 从 FrameCache 补充 post 阶段帧
                frame_cache = self.frame_caches.get(camera_id)
                post_frames = []
                if frame_cache is not None:
                    post_cached = frame_cache.get_frames(
                        end_time, end_time + post_seconds,
                        max_frames=max(1, max_frames // 3)
                    )
                    post_frames = [img for _, img in post_cached]

                # 合并帧序列
                all_frames = pre_frames + post_frames
                if len(all_frames) >= 2:
                    self.gif_executor.submit(
                        self._async_generate_gif,
                        event_output, event_id,
                        frames=all_frames, camera_id=camera_id,
                        bbox=bbox, trajectory_points=trajectory_points,
                    )
                    logger.info(
                        f"[{camera_id}] 延迟 GIF 任务已提交: "
                        f"pre={len(pre_frames)}帧, post={len(post_frames)}帧"
                    )
            except Exception as e:
                logger.error(f"[{camera_id}] 延迟 GIF 生成失败: {e}")

        # ✅ 修复：用 threading.Timer 替代 gif_executor.submit(sleep)，
        # 避免 sleep 占用 GIF worker 导致 pre_frames 引用在队列中积压（内存爆炸根因）
        timer = threading.Timer(post_seconds, _delayed_task)
        timer.daemon = True
        timer.start()

    def _async_generate_gif(self, event_output, event_id: str,
                            frames: list, camera_id: str,
                            bbox: tuple = None, trajectory_points: list = None):
        """异步生成 GIF（在线程池中执行）"""
        try:
            gif_path = event_output.generate_gif(
                event_id, frames, bbox=bbox, trajectory_points=trajectory_points
            )
            if gif_path:
                logger.info(f"[{camera_id}] GIF 生成成功: event_id={event_id[:8]}, path={gif_path}")
            else:
                logger.debug(f"[{camera_id}] GIF 生成跳过: event_id={event_id[:8]}, 帧数不足或功能未启用")
        except Exception as e:
            logger.error(f"[{camera_id}] GIF 生成失败: event_id={event_id[:8]}, error={e}")

    def _cleanup(self, proc_logger):
        """清理资源"""
        # 关闭 GIF 线程池
        if self.gif_executor:
            self.gif_executor.shutdown(wait=False)

        # 事件层合并：进程退出时强制结束所有 pending_end 事件
        # 避免宽限期内的事件丢失（未调用 _finalize_event_end 导致事件状态残留）
        if self.pending_end_events:
            proc_logger.info(
                f"清理 {sum(len(v) for v in self.pending_end_events.values())} 个 pending_end 事件"
            )
            for camera_id in list(self.pending_end_events.keys()):
                for pending_track_id in list(self.pending_end_events[camera_id].keys()):
                    try:
                        pending_info = self.pending_end_events[camera_id][pending_track_id]
                        # 使用 end_frame_time 作为当前时间（进程退出时无新帧）
                        self._finalize_event_end(camera_id, pending_track_id, pending_info)
                    except Exception as e:
                        proc_logger.error(
                            f"[{camera_id}] 清理 pending_end 事件失败: track_id={pending_track_id}, {e}"
                        )

        # 关闭 EventOutput
        for cam_id, event_output in self.event_outputs.items():
            try:
                event_output.close()
            except Exception as e:
                proc_logger.error(f"[{cam_id}] 关闭 EventOutput 失败: {e}")

        proc_logger.info("资源清理完成")


class TrackedObjectProcessorPool:
    """
    多进程事件处理器池

    将相机分配到 N 个 TrackedObjectProcessor worker 进程并行处理，
    通过分发线程按 camera_id 路由数据到对应 worker 队列。

    解决单进程处理多相机高帧率时的吞吐量瓶颈：
    16 路 × 6fps = 96 帧/秒，单进程的 shm 回读 + JPEG 编码 + AI 流推送跟不上。

    架构:
        CameraWorker(×16) → detected_objects_queue(共享)
                                  ↓
                          分发线程（主进程内）
                                ↓ 按 camera_id 路由
                     ┌──────────┬──────────┐
                     ↓          ↓          ↓
               worker_queue[0] worker_queue[1] worker_queue[N]
                     ↓          ↓          ↓
               Processor(0)  Processor(1)  Processor(N)
               (4个相机)    (4个相机)    (4个相机)

    每个 worker 是独立的 TrackedObjectProcessor 进程，拥有独立的
    CameraState/EventOutput/FrameCache/线程池，互不干扰。
    """

    def __init__(
        self,
        camera_configs: dict,
        detected_objects_queue: mp.Queue,
        stop_event: mp.Event,
        ai_stream_config: Optional[dict] = None,
        ai_stream_enabled_state: Optional[dict] = None,
        ptz_metrics: Optional[dict] = None,
        num_workers: Optional[int] = None,
    ):
        """
        Args:
            camera_configs: 全部相机配置 {camera_id: {...}}
            detected_objects_queue: CameraWorker 投递的共享队列
            stop_event: 停止事件
            ai_stream_config: AI 流配置
            ai_stream_enabled_state: AI 流启用状态（Manager dict 代理）
            ptz_metrics: PTZ 共享状态
            num_workers: worker 进程数（默认自动：每 2 相机 1 个，最少 1 最多 8）
        """
        if num_workers is None:
            num_workers = min(8, max(1, len(camera_configs) // 2))
        self.num_workers = num_workers

        self.workers: list[TrackedObjectProcessor] = []
        self.worker_queues: list[mp.Queue] = []
        self.worker_ready_events: list[mp.Event] = []
        self.camera_to_worker: dict[str, int] = {}

        # 均匀分配相机到 worker（round-robin）
        for i, cam_id in enumerate(camera_configs.keys()):
            self.camera_to_worker[cam_id] = i % num_workers

        # 为每个 worker 创建子配置、专属队列和进程
        for w in range(num_workers):
            worker_cameras = {
                cam_id: cfg
                for cam_id, cfg in camera_configs.items()
                if self.camera_to_worker[cam_id] == w
            }
            worker_queue = mp.Queue(maxsize=500)
            ready_event = mp.Event()

            worker = TrackedObjectProcessor(
                camera_configs=worker_cameras,
                detected_objects_queue=worker_queue,
                stop_event=stop_event,
                ai_stream_config=ai_stream_config,
                ai_stream_enabled_state=ai_stream_enabled_state,
                ptz_metrics=ptz_metrics,
                ready_event=ready_event,
            )
            worker.name = f"TrackedObjectProcessor-{w}"

            self.workers.append(worker)
            self.worker_queues.append(worker_queue)
            self.worker_ready_events.append(ready_event)

        self.detected_objects_queue = detected_objects_queue
        self.stop_event = stop_event
        self._dispatch_thread: Optional[threading.Thread] = None
        self._dispatch_stop = threading.Event()

        logger.info(
            f"[TrackedObjectProcessorPool] 初始化: "
            f"workers={num_workers}, cameras={len(camera_configs)}"
        )

    def start(self):
        """启动所有 worker 进程和分发线程"""
        for i, worker in enumerate(self.workers):
            worker.start()
            logger.info(
                f"[TrackedObjectProcessorPool] worker-{i} 启动 "
                f"(pid={worker.pid}, cameras={len(worker.camera_configs)})"
            )

        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            name="ProcessorDispatcher",
            daemon=True,
        )
        self._dispatch_thread.start()

    def wait_ready(self, timeout: float = 60) -> bool:
        """等待所有 worker 就绪"""
        deadline = time.time() + timeout
        for i, evt in enumerate(self.worker_ready_events):
            remaining = max(0.1, deadline - time.time())
            if not evt.wait(timeout=remaining):
                logger.warning(
                    f"[TrackedObjectProcessorPool] worker-{i} 就绪超时"
                )
                return False
        logger.info(
            f"[TrackedObjectProcessorPool] 所有 {self.num_workers} 个 worker 已就绪"
        )
        return True

    def _dispatch_loop(self):
        """
        分发线程：从主队列读取，按 camera_id 路由到 worker 队列

        分发逻辑非常轻量（dict 查找 + put），不会成为瓶颈。
        worker 队列满时丢弃本帧（背压），避免阻塞分发线程影响其他相机。
        """
        dispatch_count = 0
        drop_count = 0
        last_stats_time = time.time()

        while not self.stop_event.is_set() and not self._dispatch_stop.is_set():
            try:
                data = self.detected_objects_queue.get(timeout=1)
            except Exception:
                continue

            if not data:
                continue

            camera_id = data[0]
            worker_idx = self.camera_to_worker.get(camera_id)
            if worker_idx is None:
                continue

            try:
                self.worker_queues[worker_idx].put(data, timeout=0.05)
                dispatch_count += 1
            except Exception:
                drop_count += 1

            # 每 60 秒统计
            now = time.time()
            if now - last_stats_time > 60:
                logger.info(
                    f"[ProcessorDispatcher] 近60s: "
                    f"分发 {dispatch_count} 帧, 丢弃 {drop_count} 帧"
                )
                last_stats_time = now
                dispatch_count = 0
                drop_count = 0

        logger.info("[ProcessorDispatcher] 分发线程退出")

    def is_alive(self) -> bool:
        """检查是否有任一 worker 存活"""
        return any(w.is_alive() for w in self.workers)

    @property
    def pid(self):
        """返回第一个 worker 的 pid（用于日志）"""
        return self.workers[0].pid if self.workers else None

    def join(self, timeout=None):
        """等待所有 worker 退出"""
        self._dispatch_stop.set()
        for w in self.workers:
            w.join(timeout=timeout)

    def terminate(self):
        """终止所有 worker"""
        self._dispatch_stop.set()
        for w in self.workers:
            if w.is_alive():
                w.terminate()
