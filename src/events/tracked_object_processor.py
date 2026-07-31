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

import logging
import multiprocessing as mp
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import cv2
import numpy as np

from src.events.camera_state import CameraState

logger = logging.getLogger(__name__)


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
        """
        super().__init__(name="TrackedObjectProcessor")

        self.camera_configs = camera_configs
        self.detected_objects_queue = detected_objects_queue
        self.stop_event = stop_event
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

        # GIF 生成线程池（耗时IO操作）
        self.gif_executor: Optional[ThreadPoolExecutor] = None

        # AI 视频流异步推送线程池
        self.ai_stream_executor: Optional[ThreadPoolExecutor] = None

        # AI 视频流推送帧计数器
        self.ai_stream_frame_counters: dict[str, int] = {}

        # PTZ 自动跟踪:每个相机当前跟踪的目标 obj_id（None 表示无跟踪目标）
        self.ptz_tracked_object: dict[str, Optional[str]] = {}

        logger.info(
            f"[TrackedObjectProcessor] 初始化: "
            f"cameras={list(camera_configs.keys())}, "
            f"ptz_cameras={list(self.ptz_metrics.keys())}, "
            f"ai_stream_config={self.ai_stream_config}"
        )

    def run(self):
        """进程主函数"""
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
            max_workers=3, thread_name_prefix="AI-Stream"
        )

        # AI 视频流配置
        ai_stream_interval = self.ai_stream_config.get("interval", 10)

        proc_logger.info(
            f"事件处理进程就绪: "
            f"camera_states={len(self.camera_states)}, "
            f"ai_stream_interval={ai_stream_interval}"
        )

        # 主循环
        processed_count = 0
        last_stats_time = time.time()

        while not self.stop_event.is_set():
            try:
                # 从队列获取数据（1秒超时,便于检查 stop_event）
                data = self.detected_objects_queue.get(timeout=1)
            except Exception:
                # 队列为空或出错
                continue

            try:
                self._process_detection_data(data)
                processed_count += 1

                # 定期统计日志（每 60 秒）
                now = time.time()
                if now - last_stats_time > 60:
                    proc_logger.info(
                        f"已处理 {processed_count} 帧检测数据, "
                        f"活跃事件: {sum(len(v) for v in self.active_events.values())}"
                    )
                    last_stats_time = now

            except Exception as e:
                proc_logger.error(f"处理检测数据失败: {e}", exc_info=True)

        # 清理
        proc_logger.info("事件处理进程正在清理...")

        # 关闭 GIF 线程池
        if self.gif_executor:
            self.gif_executor.shutdown(wait=False)

        # 关闭 AI 流推送线程池
        if self.ai_stream_executor:
            self.ai_stream_executor.shutdown(wait=False)

        self._cleanup(proc_logger)
        proc_logger.info(f"事件处理进程已退出 (共处理 {processed_count} 帧)")

    def _init_components(self, proc_logger):
        """初始化 CameraState、EventOutput 和 FrameCache"""
        from src.events.event_output import EventOutput
        from src.events.frame_cache import FrameCache

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

    def _register_callbacks(self, cam_id: str):
        """为相机注册事件回调"""
        state = self.camera_states[cam_id]

        # "start" 回调:新目标出现
        state.on("start", self._on_object_start)

        # "update" 回调:目标状态显著变化
        state.on("update", self._on_object_update)

        # "end" 回调:目标消失
        state.on("end", self._on_object_end)

        # Zone 区域事件回调
        state.on("zone_enter", self._on_zone_enter)
        state.on("zone_exit", self._on_zone_exit)
        state.on("loitering", self._on_loitering)

        # "autotrack" 回调:目标位置明显移动 → 更新 PTZMetrics
        # 仅在配置了 PTZMetrics 的相机上注册（基于 src/cameras/ptz_service.py）
        if cam_id in self.ptz_metrics:
            state.on("autotrack", self._on_object_autotrack)

    def _process_detection_data(self, data: tuple):
        """
        处理从队列获取的检测数据

        数据格式: (camera_id, frame_time, tracked_objects_dict,
                   motion_boxes, regions, shm_block_id, shm_name,
                   detection_results)
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

        if camera_id not in self.camera_states:
            logger.warning(f"未知的 camera_id: {camera_id}")
            return

        # 保存当前帧的共享内存信息（用于事件回调时回读帧）
        self._current_shm_info = {
            "shm_block_id": shm_block_id,
            "shm_name": shm_name,
            "frame_time": frame_time,
        }

        # 更新 CameraState（触发事件回调）
        self.camera_states[camera_id].update(
            frame_time=frame_time,
            current_tracked_objects=tracked_objects_dict,
            motion_boxes=motion_boxes,
            regions=regions,
        )

        # 帧缓存：将当前帧写入 FrameCache（用于 GIF 帧回放）
        frame_cache = self.frame_caches.get(camera_id)
        if frame_cache is not None:
            image = self._read_frame_from_shm(camera_id)
            if image is not None:
                frame_cache.push(frame_time, image)

        # AI 视频流推送
        self._maybe_push_ai_stream(
            camera_id, frame_time, tracked_objects_dict,
            motion_boxes, regions, detection_results
        )

    def _on_object_start(self, camera_id: str, obj_dict: dict, frame_time: float):
        """新目标出现回调 - 创建跟踪事件（type=tracking, status=tracking_start）"""
        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return

        # 检查是否应该发送
        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        if not event_output.should_send(class_name, confidence):
            return

        # 根据 capture.save_full_on_start 配置决定是否回读帧生成快照
        image = None
        if event_output.capture_save_full_on_start:
            image = self._read_frame_from_shm(camera_id)

        # 构建事件数据
        track_id = obj_dict.get("id", "")
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        # ✅ 修复：事件时间使用系统 Unix 时间戳（frame_time 是视频相对时间，不是时间戳）
        wall_time = time.time()

        event_data = {
            "track_id": track_id,
            "class_name": class_name,
            "confidence": confidence,
            "bbox": bbox,
            "center": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
            "start_time": wall_time,
            "status": "tracking_start",
        }

        # 创建跟踪事件（type=tracking）
        event_id = event_output.emit(
            event_type="tracking",
            data=event_data,
            image=image,
            frame_id=obj_dict.get("frame_time", 0),
        )

        # 记录活跃事件
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

        event_id = active_event.get("event_id", "")

        # 回读帧生成快照（仅在 thumbnail 更新时）
        image = None
        if obj_dict.get("snapshot") is not None:
            image = self._read_frame_from_shm(camera_id)

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
        """目标消失回调 - 保存置信度最高的图片 + 推送 event_update 到前端

        单一事件模型：end 阶段保存 best_thumbnail（置信度最高）到存储，
        并推送 event_update（status=ended）让前端更新卡片状态。
        """
        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return

        track_id = obj_dict.get("id", "")

        # 检查是否有活跃事件
        active_event = self.active_events.get(camera_id, {}).get(track_id)
        if active_event is None:
            return

        event_id = active_event.get("event_id", "")
        event_start_time = active_event.get("start_wall_time", time.time())  # 系统 Unix 时间戳（用于数据库）
        event_start_frame_time = active_event.get("start_time", frame_time)  # 视频相对时间（用于 GIF 帧回取）

        # 构建更新数据
        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        update_data = {
            "confidence": confidence,
            "bbox": bbox,
            "class_name": class_name,
            "end_time": time.time(),         # 系统 Unix 时间戳
            "start_time": event_start_time,  # 系统 Unix 时间戳（来自 start 阶段）
        }

        # 获取置信度最高的缩略图作为结束事件图片
        best_thumbnail = event_output.get_best_thumbnail(event_id)
        best_image = None
        if best_thumbnail and best_thumbnail.get("image") is not None:
            best_image = best_thumbnail["image"]

        # 根据 capture.save_full_on_end 配置决定是否回读帧生成结束快照
        end_image = None
        if event_output.capture_save_full_on_end:
            end_image = self._read_frame_from_shm(camera_id)

        # 选择最终图片：优先使用置信度最高的缩略图，其次使用结束帧
        final_image = best_image if best_image is not None else end_image

        # ✅ 更新事件：保存置信度最高的图片 + 推送 event_update（status=tracking_end）
        event_output.update_event(
            event_id,
            status="tracking_end",
            data=update_data,
            image=final_image,
            push_websocket=True,
        )

        # 异步生成 GIF（从 FrameCache 按时间窗口回取帧，GIF生成后自动回填到事件）
        if event_output.gif_enabled and self.gif_executor:
            frame_cache = self.frame_caches.get(camera_id)
            if frame_cache is not None:
                # 按 pre_seconds / post_seconds 确定时间窗口
                gif_cfg = event_output.gif_cfg
                pre_seconds = gif_cfg.get('pre_seconds', 5.0)
                post_seconds = gif_cfg.get('post_seconds', 5.0)
                max_frames = gif_cfg.get('max_frames', 60)

                # 时间窗口：[事件开始前 pre_seconds, 当前帧]
                # 注意：使用视频相对时间（frame_time），与 FrameCache 的 key 一致
                retrieval_start = event_start_frame_time - pre_seconds
                retrieval_end = frame_time

                cached_frames = frame_cache.get_frames(
                    retrieval_start, retrieval_end, max_frames=max_frames
                )

                if cached_frames and len(cached_frames) >= 2:
                    # 提取纯帧序列
                    frames = [img for _, img in cached_frames]
                    # 获取轨迹点（用于 GIF 绘制轨迹）
                    trajectory_points = obj_dict.get("trajectory", [])

                    logger.info(
                        f"[{camera_id}] GIF 帧回取成功: {len(frames)}帧, "
                        f"时间窗口=[{retrieval_start:.1f}, {retrieval_end:.1f}], "
                        f"post_seconds={post_seconds}"
                    )

                    if post_seconds > 0:
                        # 需要等待 post_seconds 后再生成 GIF（补充消失后帧）
                        self._schedule_delayed_gif(
                            event_output, event_id, camera_id,
                            frames, bbox, trajectory_points,
                            frame_time, post_seconds, max_frames
                        )
                    else:
                        # 无需等待，立即生成
                        self.gif_executor.submit(
                            self._async_generate_gif,
                            event_output, event_id,
                            frames=frames, camera_id=camera_id,
                            bbox=bbox, trajectory_points=trajectory_points,
                        )
                else:
                    frame_count = len(cached_frames) if cached_frames else 0
                    logger.warning(
                        f"[{camera_id}] GIF 帧不足: {frame_count}帧, "
                        f"时间窗口=[{retrieval_start:.1f}, {retrieval_end:.1f}], "
                        f"需要>=2帧"
                    )
            else:
                logger.warning(f"[{camera_id}] FrameCache 不存在, 跳过 GIF 生成")
        elif not event_output.gif_enabled:
            logger.debug(f"[{camera_id}] GIF 未启用, 跳过 GIF 生成")

        # 清理缩略图
        event_output.clear_thumbnail(event_id)

        # 从活跃事件中移除
        del self.active_events[camera_id][track_id]

        # 如果消失的是当前 PTZ 跟踪目标,清除 PTZ 跟踪状态
        if self.ptz_tracked_object.get(camera_id) == track_id:
            self._clear_ptz_tracking(camera_id, frame_time)

        logger.info(
            f"[{camera_id}] 跟踪事件结束: id={track_id}, event_id={event_id[:8] if event_id else ''}, "
            f"label={class_name}, duration={frame_time - active_event['start_time']:.1f}s"
        )

    def _on_zone_enter(self, camera_id: str, obj_dict: dict, frame_time: float, zone_name: str):
        """目标进入 Zone 区域回调"""
        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return

        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        if not event_output.should_send(class_name, confidence):
            return

        track_id = obj_dict.get("id", "")
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        event_data = {
            "track_id": track_id,
            "class_name": class_name,
            "confidence": confidence,
            "bbox": bbox,
            "zone_name": zone_name,
            "event_subtype": "zone_enter",
        }

        event_output.emit(
            event_type="zone_enter",
            data=event_data,
            image=None,
            frame_id=obj_dict.get("frame_time", 0),
        )

        logger.info(
            f"[{camera_id}] 目标进入区域: id={track_id}, zone={zone_name}, label={class_name}"
        )

    def _on_zone_exit(self, camera_id: str, obj_dict: dict, frame_time: float, zone_name: str):
        """目标离开 Zone 区域回调"""
        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return

        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        if not event_output.should_send(class_name, confidence):
            return

        track_id = obj_dict.get("id", "")
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        event_data = {
            "track_id": track_id,
            "class_name": class_name,
            "confidence": confidence,
            "bbox": bbox,
            "zone_name": zone_name,
            "event_subtype": "zone_exit",
        }

        event_output.emit(
            event_type="zone_exit",
            data=event_data,
            image=None,
            frame_id=obj_dict.get("frame_time", 0),
        )

        logger.info(
            f"[{camera_id}] 目标离开区域: id={track_id}, zone={zone_name}, label={class_name}"
        )

    def _on_loitering(self, camera_id: str, obj_dict: dict, frame_time: float, zone_name: str):
        """徘徊检测回调"""
        event_output = self.event_outputs.get(camera_id)
        if event_output is None:
            return

        class_name = obj_dict.get("label", "unknown")
        confidence = obj_dict.get("score", 0.0)
        if not event_output.should_send(class_name, confidence):
            return

        track_id = obj_dict.get("id", "")
        bbox = obj_dict.get("box", [0, 0, 0, 0])

        # 回读帧生成快照
        image = self._read_frame_from_shm(camera_id)

        event_data = {
            "track_id": track_id,
            "class_name": class_name,
            "confidence": confidence,
            "bbox": bbox,
            "zone_name": zone_name,
            "event_subtype": "loitering",
        }

        event_output.emit(
            event_type="loitering",
            data=event_data,
            image=image,
            frame_id=obj_dict.get("frame_time", 0),
        )

        logger.info(
            f"[{camera_id}] 徘徊检测: id={track_id}, zone={zone_name}, label={class_name}"
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

    def _read_frame_from_shm(self, camera_id: str) -> Optional[np.ndarray]:
        """
        从共享内存回读帧

        使用当前帧的 shm_block_id 和 shm_name 回读 YUV 帧,转换为 RGB。

        Returns:
            RGB 帧 (height, width, 3),失败返回 None
        """
        shm_info = getattr(self, "_current_shm_info", None)
        if shm_info is None:
            return None

        shm_block_id = shm_info.get("shm_block_id")
        shm_name = shm_info.get("shm_name")

        if shm_block_id is None or shm_name is None:
            return None

        # 获取帧形状
        config = self.camera_configs.get(camera_id, {})
        frame_shape = config.get("frame_shape", (640, 640))

        try:
            from src.utils.shared_memory import read_frame_from_shared_memory

            rgb_frame = read_frame_from_shared_memory(
                shm_name=shm_name,
                shm_block_id=shm_block_id,
                frame_shape=frame_shape,
            )
            return rgb_frame
        except Exception as e:
            logger.debug(f"[{camera_id}] 回读帧失败: {e}")
            return None

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

    def _maybe_push_ai_stream(
        self, camera_id: str, frame_time: float, tracked_objects: dict,
        motion_boxes: list = None, regions: list = None,
        detection_results: list = None
    ):
        """
        AI 视频流推送（异步方案：后端绘制检测框，推送JPEG帧）

        流程：
        1. 检查该摄像机是否启用 AI 流（文件标记优先，Manager dict 降级兼容）
        2. 从共享内存回读帧
        3. 绘制检测框到帧上（受 display_config 开关管控）
        4. JPEG压缩
        5. 异步推送到 /api/ai-stream/push（通过线程池）
        """
        # 检查该摄像机是否启用 AI 流
        if not self._is_ai_stream_enabled(camera_id):
            # 每隔100帧打印一次日志，避免刷屏
            if not hasattr(self, '_ai_stream_check_counters'):
                self._ai_stream_check_counters = {}
            self._ai_stream_check_counters[camera_id] = self._ai_stream_check_counters.get(camera_id, 0) + 1
            if self._ai_stream_check_counters[camera_id] % 100 == 0:
                logger.info(
                    f"[{camera_id}] AI流：未启用 (检查计数: {self._ai_stream_check_counters[camera_id]})"
                )
            return

        interval = self.ai_stream_config.get("interval", 10)
        self.ai_stream_frame_counters[camera_id] = (
            self.ai_stream_frame_counters.get(camera_id, 0) + 1
        )

        if self.ai_stream_frame_counters[camera_id] % interval != 0:
            return

        # 从共享内存回读帧
        frame = self._read_frame_from_shm(camera_id)
        if frame is None:
            logger.warning(f"[{camera_id}] AI流：回读帧失败，跳过推送 (frame_seq={self.ai_stream_frame_counters[camera_id]})")
            return

        logger.debug(f"[{camera_id}] AI流：回读帧成功 shape={frame.shape}")

        # 绘制检测框（受 display_config 开关管控）
        frame_with_boxes = self._draw_detections_on_frame(
            frame, camera_id, tracked_objects, motion_boxes, regions, detection_results
        )

        # JPEG压缩
        import base64
        import cv2

        # 按原始宽高比缩放（降低带宽，最大宽度640）
        # 注意：不强制缩放到正方形，保持原始视频的宽高比，避免画面变形
        h, w = frame_with_boxes.shape[:2]
        target_max_width = 640
        if w > target_max_width:
            scale = target_max_width / w
            new_w = target_max_width
            new_h = int(h * scale)
            frame_with_boxes = cv2.resize(frame_with_boxes, (new_w, new_h))

        # JPEG编码（质量70）
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, 70]
        _, jpeg_buffer = cv2.imencode('.jpg', frame_with_boxes, encode_params)
        frame_base64 = base64.b64encode(jpeg_buffer).decode('utf-8')

        # 获取 task_id
        cam_config = self.camera_configs.get(camera_id, {})
        task_id = cam_config.get("task_id", "")

        # 异步推送到 /api/ai-stream/push
        api_url = self.ai_stream_config.get("api_url")
        frame_seq = self.ai_stream_frame_counters.get(camera_id, 0)
        
        if api_url and self.ai_stream_executor:
            logger.debug(
                f"[{camera_id}] AI流：提交推送任务 frame_seq={frame_seq} "
                f"size={len(frame_base64)}B"
            )
            self.ai_stream_executor.submit(
                self._async_push_ai_frame,
                api_url,
                task_id,
                camera_id,
                frame_seq,
                frame_base64,
            )
        else:
            logger.warning(
                f"[{camera_id}] AI流：未推送 api_url={bool(api_url)} "
                f"executor={bool(self.ai_stream_executor)}"
            )

    def _async_push_ai_frame(
        self, api_url: str, task_id: str, camera_id: str, frame_seq: int, frame_base64: str
    ):
        """异步推送 AI 帧（在线程池中执行）"""
        try:
            import requests
            response = requests.post(
                api_url,
                json={
                    "task_id": task_id,
                    "camera_id": camera_id,
                    "frame_seq": frame_seq,
                    "frame_base64": frame_base64,
                },
                timeout=2,
            )
            if response.status_code == 200:
                logger.debug(f"[{camera_id}] AI帧推送成功: frame_seq={frame_seq}")
            else:
                logger.warning(
                    f"[{camera_id}] AI帧推送失败: status={response.status_code} "
                    f"frame_seq={frame_seq}"
                )
        except Exception as e:
            # 记录网络错误（重要）
            logger.error(f"[{camera_id}] AI帧推送异常: {e} frame_seq={frame_seq}")

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
                    # 颜色（配置中为RGB格式，OpenCV使用BGR，需要转换）
                    zone_color_rgb = zone_cfg.get('color', [255, 0, 0])
                    zone_color = tuple(reversed(zone_color_rgb))
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
            motion_color = tuple(motion_cfg.get('color', [255, 255, 0]))
            motion_thickness = motion_cfg.get('thickness', 1)
            fill_alpha = motion_cfg.get('fill_alpha', 50)
            fill_color = tuple(motion_cfg.get('fill_color', [255, 255, 0]))

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
                    draw_utf8_label_on_rgb(frame_copy, text, (x1, label_y),
                                           font_size=14, text_color_rgb=tuple(det_label_color))
                else:
                    cv2.putText(frame_copy, text, (x1, y1 - 10),
                               cv2.FONT_HERSHEY_SIMPLEX, det_font_scale, det_label_color, 1)

        # ========================================
        # 3. 绘制跟踪框（受 show_track_boxes 管控）
        #    来自 tracked_objects：已进入跟踪的目标（持续≥min_initialized帧）
        # ========================================
        if show_track and tracked_objects:
            track_color = tuple(track_box_cfg.get('color', [255, 0, 0]))
            track_thickness = track_box_cfg.get('thickness', 2)
            track_font_scale = track_box_cfg.get('font_scale', 0.5)
            track_label_color = tuple(track_box_cfg.get('label_color', [255, 0, 0]))

            for obj_id, obj_dict in tracked_objects.items():
                bbox = obj_dict.get("box", [0, 0, 0, 0])
                x1, y1, x2, y2 = [int(v) for v in bbox]
                cv2.rectangle(frame_copy, (x1, y1), (x2, y2), track_color, track_thickness)
                track_text = f"ID: {obj_id[:8]}"
                # 跟踪框标签（支持中文）
                if _has_chinese_font:
                    draw_utf8_label_on_rgb(frame_copy, track_text, (x1, y2 + 5),
                                           font_size=14, text_color_rgb=tuple(track_label_color))
                else:
                    cv2.putText(frame_copy, track_text, (x1, y2 + 20),
                               cv2.FONT_HERSHEY_SIMPLEX, track_font_scale, track_label_color, 1)

        # ========================================
        # 4. 绘制轨迹（受 show_trajectories 管控）
        #    来自 tracked_objects 的轨迹历史
        # ========================================
        if show_trajectories and tracked_objects:
            traj_color = tuple(trajectory_cfg.get('color', [0, 255, 255]))
            traj_thickness = trajectory_cfg.get('thickness', 1)
            point_size = trajectory_cfg.get('point_size', 2)
            point_color = tuple(trajectory_cfg.get('point_color', [0, 0, 255]))

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

    def _schedule_delayed_gif(self, event_output, event_id: str, camera_id: str,
                              pre_frames: list, bbox: tuple, trajectory_points: list,
                              end_time: float, post_seconds: float, max_frames: int):
        """
        延迟生成 GIF（等待 post_seconds 后补充消失后帧再生成）

        使用定时线程等待 post_seconds，然后从 FrameCache 补充 post 阶段帧，
        合并 pre 阶段帧后提交 GIF 生成任务。

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
            try:
                # 等待 post_seconds 秒
                time.sleep(post_seconds)

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

        # 在 GIF 线程池中启动延迟任务
        self.gif_executor.submit(_delayed_task)

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

        # 关闭 EventOutput
        for cam_id, event_output in self.event_outputs.items():
            try:
                event_output.close()
            except Exception as e:
                proc_logger.error(f"[{cam_id}] 关闭 EventOutput 失败: {e}")

        proc_logger.info("资源清理完成")
