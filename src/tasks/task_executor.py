"""
多摄像机任务执行器

支持一个检测任务同时检测多个摄像机。
每个摄像机使用独立的解码器、运动检测器和跟踪器，共享检测器进程池。
所有配置从数据库读取，无需依赖配置文件。

架构特性：
- 多进程架构：每个摄像机一个 CameraWorker 子进程
- 同步检测：RemoteObjectDetector 通过共享内存+Event 同步检测
- Region 检测：运动框+跟踪框聚类生成 region，对每个 region 同步检测
- 事件处理独立进程：TrackedObjectProcessor 消费 detected_objects_queue
- 零拷贝传输：环形缓冲区共享内存传递视频帧
- 多模型支持：DetectorPoolManager 按 model_type 分组管理检测器进程池
"""

import sys
import os
import time
import json
import signal
import threading
import multiprocessing
from multiprocessing import Process, Queue
import queue
import numpy as np
from typing import Dict, Optional, List
import logging
import yaml
from pathlib import Path

# 配置日志
logger = logging.getLogger(__name__)

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)


def _camera_worker_process_wrapper(
    camera_config,
    task_config,
    detector_params,
    detection_queue,
    out_event,
    detected_objects_queue,
    stop_event,
    ptz_metrics=None,
):
    """
    摄像机工作进程包装函数（用于多进程架构）

    在子进程中创建并运行 CameraWorker，避免 pickle 序列化问题。
    所有组件（RemoteObjectDetector / ImprovedMotionDetector / NorfairTracker）
    都在子进程内创建，避免跨进程传递共享内存对象。

    Args:
        camera_config: 摄像机配置（可序列化）
        task_config: 任务配置（可序列化）
        detector_params: 检测器参数（用于子进程内创建 RemoteObjectDetector）
            {camera_id, model_type, class_names, input_shape, input_height, input_width}
        detection_queue: 检测任务队列（mp.Queue，与 DetectorPool 共享）
        out_event: 输出就绪事件（mp.Event，该相机的专属事件）
        detected_objects_queue: 事件处理队列（mp.Queue，输出给 TrackedObjectProcessor）
        stop_event: 停止事件（mp.Event）
        ptz_metrics: PTZ 共享状态（可选）
    """
    # 子进程日志配置：用 QueueHandler 把日志发到主进程，避免多进程写同一文件
    # Windows spawn 模式下子进程会继承主进程的 RotatingFileHandler，多进程同时
    # 写同一日志文件会触发 PermissionError [WinError 32]（文件锁冲突）
    try:
        from src.utils.log_manager import LogManager
        LogManager.get_instance().setup_worker_process()
    except Exception:
        pass

    try:
        worker = CameraWorker(
            camera_config=camera_config,
            task_config=task_config,
            detector_params=detector_params,
            detection_queue=detection_queue,
            out_event=out_event,
            detected_objects_queue=detected_objects_queue,
            stop_event=stop_event,
            ptz_metrics=ptz_metrics,
        )
        worker.run()
    except Exception as e:
        camera_id = camera_config.get('camera_id', 'unknown')
        logger.debug(f"[CameraWorker-{camera_id}] 进程异常退出: {e}")
        import traceback
        traceback.print_exc()


class CameraWorker:
    """单个摄像机的检测工作进程"""

    def __init__(
        self,
        camera_config: Dict,
        task_config: Dict,
        detector_params: Dict,
        detection_queue,
        out_event,
        detected_objects_queue,
        stop_event,
        ptz_metrics=None,
    ):
        """
        初始化摄像机工作进程

        Args:
            camera_config: 摄像机配置
            task_config: 任务配置（从数据库读取的完整配置）
            detector_params: 检测器参数（用于子进程内创建 RemoteObjectDetector）
            detection_queue: 检测任务队列（与 DetectorPool 共享）
            out_event: 输出就绪事件（该相机的专属事件）
            detected_objects_queue: 事件处理队列（输出给 TrackedObjectProcessor）
            stop_event: 停止事件
            ptz_metrics: PTZ 共享状态（可选）
        """
        self.camera_config = camera_config
        self.task_config = task_config
        self.detector_params = detector_params
        self.detection_queue = detection_queue
        self.out_event = out_event
        self.detected_objects_queue = detected_objects_queue
        self.stop_event = stop_event
        self.ptz_metrics = ptz_metrics

        # 基本信息
        self.camera_id = camera_config.get('camera_id', 'unknown')
        self.rtsp_url = camera_config.get('rtsp_url', '')
        self.task_id = task_config.get('id')
        self.task_name = task_config.get('task_name', 'unknown')
        self.algorithm = task_config.get('algorithm', 'yolov11')
        self.running = False

        # 配置块
        self.decoder_cfg = task_config.get('decoder_config', {})
        self.tracker_cfg = task_config.get('tracker_config', {})
        self.motion_cfg = task_config.get('motion_config', {})
        # 功能开关
        self.motion_detection_enabled = task_config.get('motion_detection_enabled', True)
        self.tracking_enabled = task_config.get('tracking_enabled', True)
        # zones_config 是通道级配置（存储于 device_channel_pool.zones_config），
        # 由 run.py 通过 task_manager.get_channel_zones() 读取并附加到 camera_config。
        # task_config 是任务级配置，不含通道级 zones_config，必须从 camera_config 读取。
        self.zones_config = camera_config.get('zones_config', [])

        # 组件（在 run() 中创建）
        self.decoder = None
        self.remote_detector = None
        self.motion_detector = None
        self.tracker = None

        # 视频源原始尺寸（用于 AI 流还原）
        self.zlm_original_width = 0
        self.zlm_original_height = 0
        self.source_original_width = 0
        self.source_original_height = 0

        logger.debug(f"[CameraWorker-{self.camera_id}] 初始化")
        logger.debug(f"[CameraWorker-{self.camera_id}]   RTSP: {self.rtsp_url}")
        logger.debug(f"[CameraWorker-{self.camera_id}]   任务名称: {self.task_name}")
        logger.debug(f"[CameraWorker-{self.camera_id}]   算法: {self.algorithm}")
        logger.debug(f"[CameraWorker-{self.camera_id}]   模型类型: {detector_params.get('model_type', 'unknown')}")

    def init_components(self):
        """
        初始化摄像机专属组件（仅解码器）

        其他组件（RemoteObjectDetector / ImprovedMotionDetector / NorfairTracker）
        在 run() 方法中创建，避免跨进程传递共享内存对象。
        """
        try:
            # ========================================
            # 检查摄像机类型（视频文件跳过 ZLM 推流）
            # ========================================
            camera_protocol = self.camera_config.get('protocol', 'rtsp')
            is_video_file = camera_protocol == 'file'

            if is_video_file:
                logger.debug(f"[CameraWorker-{self.camera_id}] 视频文件模式，跳过 ZLM 推流检查")

                # 获取视频文件原始分辨率（用于 AI 流还原）
                try:
                    from src.utils.video_utils import get_video_original_resolution

                    video_file = self.camera_config.get('video_file', '') or self.rtsp_url
                    if video_file.startswith('file://'):
                        video_file = video_file[7:]

                    if video_file:
                        self.source_original_width, self.source_original_height = get_video_original_resolution(video_file)
                        if self.source_original_width > 0 and self.source_original_height > 0:
                            logger.debug(f"[CameraWorker-{self.camera_id}] 视频文件原始尺寸: {self.source_original_width}x{self.source_original_height}")
                    else:
                        logger.debug(f"[CameraWorker-{self.camera_id}] 警告: 视频文件路径为空")
                except Exception as e:
                    logger.debug(f"[CameraWorker-{self.camera_id}] 获取视频文件信息失败: {e}")
            else:
                # 检查 ZLM 推流状态
                logger.debug(f"[CameraWorker-{self.camera_id}] 检查 ZLM 推流状态...")
                try:
                    from src.cameras.stream_service import StreamService, check_stream_online, get_stream_media_info
                    from src.cameras.camera_manager import get_camera_manager

                    camera_manager = get_camera_manager()
                    camera = camera_manager.get_camera(self.camera_id)

                    if camera:
                        stream_service = StreamService(db_path='data/tasks.db')
                        server = stream_service._get_streaming_server()

                        if server:
                            server_host = server.get('host', '127.0.0.1')
                            http_port = server.get('http_port', server.get('api_port', 88))
                            secret = server.get('secret') or server.get('config', {}).get('secret', '')
                            app = StreamService.STREAM_APP
                            stream_id = self.camera_id

                            stream_exists = check_stream_online(server_host, http_port, secret, app, stream_id)

                            if stream_exists:
                                logger.debug(f"[CameraWorker-{self.camera_id}] ZLM 推流已存在（流已在线）")
                            else:
                                logger.debug(f"[CameraWorker-{self.camera_id}] ZLM 推流不存在，启动推流...")
                                result = stream_service.start_stream(self.camera_id)
                                if result.get('success'):
                                    logger.debug(f"[CameraWorker-{self.camera_id}] ZLM 推流已启动")
                                    stream_exists = True
                                else:
                                    logger.warning(f"[CameraWorker-{self.camera_id}] ZLM 推流启动失败: {result.get('message', '未知错误')}")

                            # 获取 ZLM 流的原始尺寸（用于坐标映射）
                            if stream_exists:
                                try:
                                    media_info = get_stream_media_info(
                                        server_host=server_host,
                                        http_port=http_port,
                                        secret=secret,
                                        app=app,
                                        stream_id=stream_id
                                    )
                                    if media_info.get('success'):
                                        self.zlm_original_width = media_info['width']
                                        self.zlm_original_height = media_info['height']
                                        logger.debug(f"[CameraWorker-{self.camera_id}] ZLM 原始尺寸: {self.zlm_original_width}x{self.zlm_original_height}")
                                except Exception as e:
                                    logger.debug(f"[CameraWorker-{self.camera_id}] 获取 ZLM 原始尺寸失败: {e}")
                        else:
                            logger.error(f"[CameraWorker-{self.camera_id}] 没有配置流媒体服务器，无法注册 AI 流")
                    else:
                        logger.warning(f"[CameraWorker-{self.camera_id}] 摄像机信息获取失败，跳过 ZLM 推流注册")
                except Exception as stream_err:
                    logger.warning(f"[CameraWorker-{self.camera_id}] ZLM 推流检查异常: {stream_err}")

            # ========================================
            # 初始化解码器（区分 RTSP 流和视频文件）
            # ========================================
            from src.decoders.video_decoder import VideoDecoder

            camera_protocol = self.camera_config.get('protocol', 'rtsp')
            is_video_file = camera_protocol == 'file'

            # 解码器配置独立于检测配置：hwaccel_preset 直接读取 decoder_config，
            # 不因 detector.backend=rknn 自动改写（解码负责解码，检测配置负责预处理+推理）
            decoder_config = {
                'width': self.decoder_cfg.get('width', 640),
                'height': self.decoder_cfg.get('height', 640),
                'fps': self.decoder_cfg.get('fps', 25),
                'hwaccel_preset': self.decoder_cfg.get('hwaccel_preset', 'preset-dxva2'),
                'rtsp_transport': self.decoder_cfg.get('rtsp_transport', 'tcp'),
                'reconnect': self.decoder_cfg.get('reconnect', True),
                'reconnect_delay': self.decoder_cfg.get('reconnect_delay', 5),
                'buffer_size': self.decoder_cfg.get('buffer_size', 1048576),
                'frame_queue_size': self.decoder_cfg.get('frame_queue_size', 30),

                # 新架构配置参数
                'use_ring_buffer': self.decoder_cfg.get('use_ring_buffer', True),
                # 环形缓冲区块数：需覆盖事件进程回读延迟（队列+检测+跟踪+事件回调+SHM回读）
                # 16相机@6fps场景下事件链路延迟约1.5-2秒，40块可缓存约6.7秒，留足余量
                'ring_buffer_block_count': self.decoder_cfg.get('ring_buffer_block_count', 40),
                'index_queue_size': self.decoder_cfg.get('index_queue_size', 30),

                # 摄像机 ID 和任务 ID（避免共享内存冲突）
                'camera_id': self.camera_id,
                'task_id': str(self.task_id) if self.task_id else ''
            }

            logger.debug(f"[CameraWorker-{self.camera_id}] 创建 decoder_config:")
            logger.debug(f"  camera_id={self.camera_id}, task_id={self.task_id}")
            logger.debug(f"  共享内存名称前缀: sagesight_{str(self.task_id) if self.task_id else ''}_{self.camera_id}_block_*")

            # 根据类型设置输入源
            if is_video_file:
                decoder_config['video_file'] = self.rtsp_url
                decoder_config['rtsp_url'] = ''
                logger.debug(f"[CameraWorker-{self.camera_id}] 视频文件输入: {self.rtsp_url}")
            else:
                decoder_config['rtsp_url'] = self.rtsp_url
                decoder_config['video_file'] = ''
                logger.debug(f"[CameraWorker-{self.camera_id}] RTSP 流输入: {self.rtsp_url}")

            # 仅创建解码器对象，不启动：避免在 pipeline 组件（RemoteObjectDetector/
            # MotionDetector/NorfairTracker）创建完成前 FFmpeg 就开始写帧，
            # 导致环形缓冲区被覆盖多圈、事件进程回读时帧序号全部不匹配
            self.decoder = VideoDecoder(decoder_config)

            logger.debug(f"[CameraWorker-{self.camera_id}] 解码器初始化成功（未启动，等待 pipeline 组件就绪后启动）")
            logger.debug(f"[CameraWorker-{self.camera_id}]   分辨率: {decoder_config['width']}x{decoder_config['height']}")
            logger.debug(f"[CameraWorker-{self.camera_id}]   硬件加速: {decoder_config['hwaccel_preset']}")
            logger.debug(f"[CameraWorker-{self.camera_id}]   环形缓冲区块数: {decoder_config['ring_buffer_block_count']}")

            return True

        except Exception as e:
            logger.debug(f"[CameraWorker-{self.camera_id}] 初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def run(self):
        """
        运行摄像机检测

        流程：
        1. init_components 创建解码器对象（不启动，避免 FFmpeg 提前写帧导致环形缓冲区被覆盖）
        2. 在子进程内创建 RemoteObjectDetector / ImprovedMotionDetector / NorfairTracker
        3. pipeline 组件就绪后启动解码器（decoder.start），确保首帧即可被消费
        4. 调用 pipeline_func 主循环（按检测类型选择流水线）

        批处理（file/scheduled）：跳过 init_components，不创建解码器，由批处理流水线内部
        按文件列表创建 VideoDecoder（每个文件一解码器，EOF 自动退出）。
        """
        self.running = True

        detection_type = self.task_config.get('detection_type', 'realtime')
        is_batch = detection_type in ('file', 'scheduled')

        if not is_batch and not self.init_components():
            logger.debug(f"[CameraWorker-{self.camera_id}] 初始化失败，退出")
            return

        logger.debug(f"[CameraWorker-{self.camera_id}] 开始检测")

        try:
            # ========================================
            # 在子进程内创建组件（避免跨进程传递共享内存对象）
            # ========================================
            from src.detectors.remote_detector import RemoteObjectDetector
            from src.motion.improved_motion import ImprovedMotionDetector
            from src.tracking.norfair_tracker import NorfairTracker
            from src.pipeline.camera_pipeline import run_pipeline

            # 帧形状和模型尺寸
            self.frame_shape = (
                self.decoder_cfg.get('height', 640),
                self.decoder_cfg.get('width', 640)
            )
            model_h = self.detector_params.get('input_height', 640)
            model_w = self.detector_params.get('input_width', 640)
            self.model_size = (model_h, model_w)

            # 检测阈值和目标类别
            detector_config_block = self.task_config.get('detector_config', {})
            self.detect_threshold = detector_config_block.get('conf_threshold', 0.4)
            target_classes = self.task_config.get('target_classes', [])
            self.target_classes = target_classes if target_classes else None

            # 推理后端（决定是否启用 RGA 硬件预处理：backend=rknn 时启用）
            self.backend = detector_config_block.get('backend', 'onnx_cuda')

            # ========================================
            # 1. 创建 RemoteObjectDetector（连接主进程已建的共享内存）
            # ========================================
            logger.debug(f"[CameraWorker-{self.camera_id}] 创建 RemoteObjectDetector...")
            self.remote_detector = RemoteObjectDetector(
                camera_id=self.camera_id,
                model_type=self.detector_params['model_type'],
                class_names=self.detector_params['class_names'],
                detection_queue=self.detection_queue,
                event=self.out_event,
                input_shape=(1, model_h, model_w, 3),
                stop_event=self.stop_event,
                task_id=str(self.task_id) if self.task_id else '',
            )
            logger.debug(f"[CameraWorker-{self.camera_id}] RemoteObjectDetector 创建成功")

            # ========================================
            # 2. 创建 ImprovedMotionDetector（帧差法）
            # ========================================
            if self.motion_detection_enabled:
                logger.debug(f"[CameraWorker-{self.camera_id}] 创建 ImprovedMotionDetector...")
                self.motion_detector = ImprovedMotionDetector(
                    frame_shape=self.frame_shape,
                    motion_height=self.motion_cfg.get('motion_height', 180),
                    threshold=self.motion_cfg.get('threshold', 25),
                    contour_area=self.motion_cfg.get('contour_area', 200),
                    improve_contrast=self.motion_cfg.get('improve_contrast', True),
                    blur_radius=self.motion_cfg.get('blur_radius', 1),
                    frame_alpha=self.motion_cfg.get('frame_alpha', 0.2),
                    contrast_frame_history=self.motion_cfg.get('contrast_frame_history', 50),
                    lightning_threshold=self.motion_cfg.get('lightning_threshold', 0.8),
                )
                logger.debug(f"[CameraWorker-{self.camera_id}] ImprovedMotionDetector 创建成功")
            else:
                self.motion_detector = None
                logger.info(f"[CameraWorker-{self.camera_id}] 运动检测已关闭，跳过创建 ImprovedMotionDetector")

            # ========================================
            # 3. 创建 NorfairTracker
            # ========================================
            if self.tracking_enabled:
                logger.debug(f"[CameraWorker-{self.camera_id}] 创建 NorfairTracker...")
                detect_config = {
                    'min_initialized': self.tracker_cfg.get('n_init', 3),
                    'max_disappeared': self.tracker_cfg.get('max_age', 30),
                    'stationary_threshold': self.task_config.get('stationary_threshold', 10),
                    'threshold': self.detect_threshold,
                    'max_frames': self.task_config.get('max_frames', {}),
                    'fps': self.decoder_cfg.get('fps', 25),
                }
                self.tracker = NorfairTracker(
                    camera_id=self.camera_id,
                    frame_shape=self.frame_shape,
                    detect_config=detect_config,
                    ptz_metrics=self.ptz_metrics,
                    zones_config=self.zones_config,
                )
                logger.debug(f"[CameraWorker-{self.camera_id}] NorfairTracker 创建成功")
            else:
                self.tracker = None
                logger.info(f"[CameraWorker-{self.camera_id}] 目标跟踪已关闭，跳过创建 NorfairTracker")
            # 打印 zones_config 加载情况，便于确认通道级 zone 配置（含徘徊设置）是否正确传入
            if self.zones_config:
                zones_info = ", ".join(
                    f"{z.get('name', '?')}"
                    f"(loiter={z.get('enable_loitering_detection', False)}"
                    f"/{z.get('loitering_time', 0)}s)"
                    for z in self.zones_config
                )
                logger.info(f"[CameraWorker-{self.camera_id}] zones_config 加载: {len(self.zones_config)} 个区域: {zones_info}")
            else:
                logger.info(f"[CameraWorker-{self.camera_id}] zones_config 为空，zone 检测与徘徊告警将不可用")

            # ========================================
            # 4. 按检测类型启动流水线
            # ========================================
            from src.pipeline.pipeline_factory import get_pipeline

            if is_batch:
                # 批处理（file/scheduled）：不启动实时解码器，由批处理流水线内部按文件创建解码器
                pipeline_func = get_pipeline(detection_type)  # 返回 run_batch_pipeline
                decoder_config = {
                    'width': self.decoder_cfg.get('width', 640),
                    'height': self.decoder_cfg.get('height', 640),
                    'fps': self.decoder_cfg.get('fps', 25),
                    'hwaccel_preset': self.decoder_cfg.get('hwaccel_preset', 'preset-dxva2'),
                    'rtsp_transport': self.decoder_cfg.get('rtsp_transport', 'tcp'),
                    'reconnect': False,  # 文件模式不重连，EOF 后结束
                    'buffer_size': self.decoder_cfg.get('buffer_size', 1048576),
                    'use_ring_buffer': self.decoder_cfg.get('use_ring_buffer', True),
                    'ring_buffer_block_count': self.decoder_cfg.get('ring_buffer_block_count', 40),
                    'index_queue_size': self.decoder_cfg.get('index_queue_size', 30),
                    'camera_id': self.camera_id,
                    'task_id': str(self.task_id) if self.task_id else '',
                }
                # 解析批处理文件列表：优先扫描 schedule_source 目录（scheduled 扫描来源），
                # 其次使用 task_config.file_paths，最后回退到通道 rtsp_url
                file_paths = self._resolve_batch_file_paths()
                file_loop = bool(self.task_config.get('file_loop', False))
                logger.info(
                    f"[CameraWorker-{self.camera_id}] 批处理启动: "
                    f"files={len(file_paths)}, file_loop={file_loop}"
                )
                pipeline_func(
                    camera_id=self.camera_id,
                    task_id=str(self.task_id),
                    decoder_config=decoder_config,
                    remote_detector=self.remote_detector,
                    motion_detector=self.motion_detector,
                    tracker=self.tracker,
                    detected_objects_queue=self.detected_objects_queue,
                    frame_shape=self.frame_shape,
                    model_size=self.model_size,
                    detect_threshold=self.detect_threshold,
                    stop_event=self.stop_event,
                    target_classes=self.target_classes,
                    backend=self.backend,
                    file_paths=file_paths,
                    file_loop=file_loop,
                )
                return

            # 实时/轮询/运动门控：启动解码器并调用对应流水线
            logger.info(f"[CameraWorker-{self.camera_id}] pipeline 组件就绪，启动解码器")
            self.decoder.start()

            logger.debug(f"[CameraWorker-{self.camera_id}] 启动检测管道主循环")
            logger.debug(f"[CameraWorker-{self.camera_id}]   frame_shape={self.frame_shape}")
            logger.debug(f"[CameraWorker-{self.camera_id}]   model_size={self.model_size}")
            logger.debug(f"[CameraWorker-{self.camera_id}]   detect_threshold={self.detect_threshold}")
            logger.debug(f"[CameraWorker-{self.camera_id}]   target_classes={self.target_classes}")
            logger.debug(f"[CameraWorker-{self.camera_id}]   backend={self.backend}")

            pipeline_func = get_pipeline(detection_type)

            pipeline_kwargs = {
                'camera_id': self.camera_id,
                'task_id': str(self.task_id),
                'decoder': self.decoder,
                'motion_detector': self.motion_detector,
                'remote_detector': self.remote_detector,
                'tracker': self.tracker,
                'detected_objects_queue': self.detected_objects_queue,
                'frame_shape': self.frame_shape,
                'model_size': self.model_size,
                'detect_threshold': self.detect_threshold,
                'stop_event': self.stop_event,
                'ptz_metrics': self.ptz_metrics,
                'target_classes': self.target_classes,
                'backend': self.backend,
            }
            if detection_type == 'polling':
                pipeline_kwargs['polling_interval_frames'] = self.task_config.get(
                    'polling_interval_frames', 5
                )
            elif detection_type == 'motion_gated':
                pipeline_kwargs['motion_gate_fallback_interval'] = self.task_config.get(
                    'motion_gate_fallback_interval', 30
                )

            logger.info(
                f"[CameraWorker-{self.camera_id}] 选择流水线: detection_type={detection_type}, "
                f"pipeline={pipeline_func.__name__}"
            )
            pipeline_func(**pipeline_kwargs)

        except KeyboardInterrupt:
            logger.debug(f"[CameraWorker-{self.camera_id}] 收到键盘中断，退出")
        except Exception as e:
            logger.debug(f"[CameraWorker-{self.camera_id}] 运行异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.cleanup()

        logger.debug(f"[CameraWorker-{self.camera_id}] 已退出")

    def _resolve_batch_file_paths(self) -> List[str]:
        """解析批处理（file/scheduled）待处理文件列表

        优先顺序：
        1. schedule_source（scheduled 扫描来源目录）：扫描该目录下的视频文件
        2. task_config.file_paths（前端填写的文件路径列表）
        3. 通道 rtsp_url（兜底，兼容旧配置）

        Returns:
            文件路径列表（已去重、过滤存在性失败项由批处理流水线自行处理）
        """
        # 1. 扫描来源目录（scheduled）
        schedule_source = (self.task_config.get('schedule_source') or '').strip()
        if schedule_source:
            dir_path = schedule_source.replace('file://', '') if schedule_source.startswith('file://') else schedule_source
            if os.path.isdir(dir_path):
                VIDEO_EXTS = ('.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.ts', '.m4v', '.webm')
                candidates = []
                try:
                    for name in sorted(os.listdir(dir_path)):
                        if name.lower().endswith(VIDEO_EXTS):
                            candidates.append(os.path.join(dir_path, name))
                except OSError as e:
                    logger.warning(f"[CameraWorker-{self.camera_id}] 扫描目录失败: {dir_path} ({e})")
                if candidates:
                    logger.info(
                        f"[CameraWorker-{self.camera_id}] schedule_source 扫描到 {len(candidates)} "
                        f"个视频文件: {dir_path}"
                    )
                    return candidates
                logger.warning(
                    f"[CameraWorker-{self.camera_id}] schedule_source 目录无视频文件，"
                    f"回退到 file_paths: {dir_path}"
                )
            else:
                logger.warning(
                    f"[CameraWorker-{self.camera_id}] schedule_source 不是有效目录，"
                    f"回退到 file_paths: {schedule_source}"
                )

        # 2. 前端填写的文件路径列表
        file_paths = self.task_config.get('file_paths') or []
        file_paths = [p for p in file_paths if p]

        # 3. 兜底：通道 rtsp_url
        if not file_paths and self.rtsp_url:
            file_paths = [self.rtsp_url]

        return file_paths

    def cleanup(self):
        """清理资源"""
        try:
            if self.remote_detector:
                self.remote_detector.cleanup()
        except Exception:
            pass

        try:
            if self.decoder:
                self.decoder.stop()
        except Exception:
            pass

        logger.debug(f"[CameraWorker-{self.camera_id}] 资源已清理")

    def stop(self):
        """停止工作进程"""
        self.running = False
        if self.stop_event:
            self.stop_event.set()
        logger.debug(f"[CameraWorker-{self.camera_id}] 收到停止信号")


class MultiCameraTaskExecutor:
    """多摄像机任务执行器 - 一个任务同时检测多个摄像机"""

    def __init__(self, task_config: Dict, cameras: List[Dict], ai_stream_enabled_state=None):
        """
        初始化多摄像机任务执行器

        Args:
            task_config: 任务配置（检测算法配置）
            cameras: 摄像机列表 [{camera_id, rtsp_url, ...}]
            ai_stream_enabled_state: 跨进程共享的 AI 流启用状态 dict（Manager dict 代理）
                前端通过 /api/ai-stream/enable|disable 控制此状态，
                TrackedObjectProcessor 读取此状态决定是否推送 AI 帧。
        """
        self.task_config = task_config
        self.cameras = cameras

        self.task_id = task_config.get('id')
        self.task_name = task_config.get('task_name', 'unknown')
        self.algorithm = task_config.get('algorithm', 'yolov11')
        self.running = False

        # 批处理模式（file / scheduled）：工作进程处理完文件后自动退出，
        # 主循环需等待事件队列排空后再停止，避免丢失事件
        self.is_batch = task_config.get('detection_type', 'realtime') in ('file', 'scheduled')

        # 摄像机工作进程（多进程架构）
        self.worker_processes: Dict[str, Process] = {}

        # 新架构组件
        self.detector_pool_manager = None
        self.detected_objects_queue = None
        self.stop_event = None
        self.tracked_object_processor = None
        self.ptz_metrics = {}
        self.detector_params_by_camera: Dict[str, Dict] = {}
        self.out_events: Dict[str, object] = {}

        # AI 流推送共享状态（跨进程，由前端 enable/disable API 控制）
        self.ai_stream_enabled_state = ai_stream_enabled_state

        logger.debug(f"[MultiCameraExecutor-{self.task_id}] 初始化")
        logger.debug(f"[MultiCameraExecutor-{self.task_id}]   任务名称: {self.task_name}")
        logger.debug(f"[MultiCameraExecutor-{self.task_id}]   算法: {self.algorithm}")
        logger.debug(f"[MultiCameraExecutor-{self.task_id}]   摄像机数量: {len(cameras)}")

    def init_shared_components(self):
        """初始化共享组件（检测器进程池 + 事件处理进程）"""
        try:
            # ========================================
            # 步骤1：从算法库读取默认配置
            # ========================================
            algorithm_config = {}
            try:
                import sqlite3
                conn = sqlite3.connect('data/tasks.db')
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT model_path, model_type, labels_file, input_size
                    FROM algorithm_configs
                    WHERE name = ?
                ''', (self.algorithm,))
                row = cursor.fetchone()
                if row:
                    algorithm_config = {
                        'model_path': row[0] or '',
                        'model_type': row[1] or '',
                        'labels_file': row[2] or '',
                        'input_size': row[3] or '640x640'
                    }
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}] 从算法库读取配置:")
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}]   模型路径: {algorithm_config['model_path']}")
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}]   模型类型: {algorithm_config['model_type']}")
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}]   标签文件: {algorithm_config['labels_file']}")
                conn.close()
            except Exception as e:
                logger.debug(f"[MultiCameraExecutor-{self.task_id}] 警告: 无法从算法库读取配置: {e}")

            # 从配置块中读取检测器参数
            detector_config_block = self.task_config.get('detector_config', {})

            # ========================================
            # 步骤2：合并配置
            # 算法库为单一真相源：model_path 和 labels_file 优先从算法库读取
            # 避免任务表中的旧路径在算法库更新后失效（如标签文件被重新上传）
            # ========================================
            detector_config = {
                'model_path': algorithm_config.get('model_path') or self.task_config.get('model_path') or 'models/detection/yolov11n.onnx',
                'target_classes': self.task_config.get('target_classes', []),  # 空列表表示不限制检测类别
                'conf_threshold': detector_config_block.get('conf_threshold', 0.25),
                'iou_threshold': detector_config_block.get('iou_threshold', 0.35),
                'min_box_area': detector_config_block.get('min_box_area', 50),
                'backend': detector_config_block.get('backend', 'onnx_cuda'),
                'use_fp16': detector_config_block.get('use_fp16', True),
                'engine_cache_path': detector_config_block.get('engine_cache_path', './trt_cache'),
                'device_id': detector_config_block.get('device_id', 0),
                'gpu_mem_limit': detector_config_block.get('gpu_mem_limit', 0),
                'tensorrt_lib_path': detector_config_block.get('tensorrt_lib_path', ''),
                'tensorrt_engine_path': detector_config_block.get('tensorrt_engine_path', ''),
                'rknn_target': detector_config_block.get('rknn_target', 'rk3588'),
                'rknn_device_id': detector_config_block.get('rknn_device_id', ''),
                'rknn_core_mask': detector_config_block.get('rknn_core_mask', 0),
                'labels_file': algorithm_config.get('labels_file') or self.task_config.get('labels_file') or detector_config_block.get('labels_file', '')
            }

            logger.info(f"[MultiCameraExecutor-{self.task_id}] 算法={self.algorithm}, 模型路径={detector_config['model_path']}, 标签文件={detector_config['labels_file'] or '未配置'}")

            # ========================================
            # 步骤2.5：PT 模型自动转换
            # 若算法上传的是 PyTorch (.pt/.pth) 模型，则在任务启动时按后端自动转换为
            # ONNX 或 RKNN，转换结果按（源文件内容 + 目标格式 + 参数）缓存，避免重复转换。
            # ========================================
            if detector_config['model_path'].lower().endswith(('.pt', '.pth')):
                try:
                    from src.utils.model_converter import ensure_converted_model
                    _input_size = algorithm_config.get('input_size') or '640x640'
                    detector_config['model_path'] = ensure_converted_model(
                        model_path=detector_config['model_path'],
                        backend=detector_config['backend'],
                        input_size=_input_size,
                        rknn_target=detector_config.get('rknn_target', 'rk3588'),
                        model_type=algorithm_config.get('model_type') or 'yolo11',
                    )
                    logger.info(
                        f"[MultiCameraExecutor-{self.task_id}] PT 模型转换完成, 实际使用模型: "
                        f"{detector_config['model_path']}"
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"[MultiCameraExecutor-{self.task_id}] PT 模型自动转换失败: {e}"
                    ) from e

            # ========================================
            # 步骤3：从标签文件加载类别名称
            # ========================================
            class_names = []
            labels_file = detector_config.get('labels_file', '')

            if labels_file and os.path.exists(labels_file):
                try:
                    with open(labels_file, 'r', encoding='utf-8') as f:
                        class_names = [line.strip() for line in f if line.strip()]
                    logger.info(f"[MultiCameraExecutor-{self.task_id}] 从标签文件加载类别: {len(class_names)} 个类别, 前5个: {class_names[:5]}")
                    if class_names:
                        logger.debug(f"[MultiCameraExecutor-{self.task_id}]   前5个类别: {class_names[:5]}")
                except Exception as e:
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}] 加载标签文件失败: {e}")
            else:
                logger.debug(f"[MultiCameraExecutor-{self.task_id}] 未配置标签文件，将使用默认类别映射")

            # 将类别列表添加到 task_config，供 CameraWorker 使用
            self.task_config['class_names'] = tuple(class_names) if class_names else ()

            # ========================================
            # 校验 target_classes 与当前标签文件的一致性
            # 标签文件语言变更后（如英文→中文），旧任务的 target_classes 会全部失效，
            # 导致所有检测被过滤掉。此时自动清除过滤，保证事件正常生成。
            # 同步处理两处过滤：任务级 target_classes + event_config.filter.target_classes
            # ========================================
            _target_classes = self.task_config.get('target_classes', [])
            if class_names and _target_classes:
                valid = [c for c in _target_classes if c in class_names]
                if not valid:
                    logger.warning(
                        f"[MultiCameraExecutor-{self.task_id}] target_classes={_target_classes} "
                        f"在当前标签文件中均不存在（标签可能已变更语言），自动清除类别过滤"
                    )
                    self.task_config['target_classes'] = []
                elif len(valid) < len(_target_classes):
                    logger.warning(
                        f"[MultiCameraExecutor-{self.task_id}] target_classes 部分标签不存在，已修正: "
                        f"{_target_classes} → {valid}"
                    )
                    self.task_config['target_classes'] = valid

            # 同步校验 event_config.filter.target_classes（EventOutput 的独立过滤）
            _event_config = self.task_config.get('event_config', {})
            _filter_cfg = _event_config.get('filter', {})
            _filter_target = _filter_cfg.get('target_classes', [])
            if class_names and _filter_target:
                valid_f = [c for c in _filter_target if c in class_names]
                if not valid_f:
                    logger.warning(
                        f"[MultiCameraExecutor-{self.task_id}] event_config.filter.target_classes={_filter_target} "
                        f"在当前标签文件中均不存在，自动清除事件过滤"
                    )
                    _filter_cfg['target_classes'] = []
                    _event_config['filter'] = _filter_cfg
                    self.task_config['event_config'] = _event_config
                elif len(valid_f) < len(_filter_target):
                    logger.warning(
                        f"[MultiCameraExecutor-{self.task_id}] event_config.filter.target_classes 部分标签不存在，已修正: "
                        f"{_filter_target} → {valid_f}"
                    )
                    _filter_cfg['target_classes'] = valid_f
                    _event_config['filter'] = _filter_cfg
                    self.task_config['event_config'] = _event_config

            # ========================================
            # 步骤4：算法名称映射
            # ========================================
            algorithm_name = self.algorithm
            algorithm_mapping = {
                'YOLOv11n': 'yolov11', 'YOLOv11s': 'yolov11', 'YOLOv11m': 'yolov11',
                'YOLOv11l': 'yolov11', 'YOLOv11x': 'yolov11',
                'YOLOv8n': 'yolov8', 'YOLOv8s': 'yolov8', 'YOLOv8m': 'yolov8',
                'YOLOv8l': 'yolov8', 'YOLOv8x': 'yolov8',
                'YOLOv5n': 'yolo', 'YOLOv5s': 'yolo', 'YOLOv5m': 'yolo',
                'YOLOv5l': 'yolo', 'YOLOv5x': 'yolo',
            }
            factory_algorithm = algorithm_mapping.get(algorithm_name, algorithm_name)
            logger.debug(f"[MultiCameraExecutor-{self.task_id}] 算法名称映射: '{algorithm_name}' -> '{factory_algorithm}'")

            # ========================================
            # 步骤5：初始化 DetectorPoolManager（新架构）
            # ========================================
            from src.detectors.detector_pool import DetectorPoolManager

            self.detector_pool_manager = DetectorPoolManager()

            # 提取所有摄像机 ID（过滤非法值：仅保留非空字符串）
            camera_ids = []
            for camera in self.cameras:
                raw_id = camera.get('camera_id')
                # 防御性检查：camera_id 必须是非空字符串，且不能是 "[]" 或 "None"
                if raw_id is None:
                    continue
                # 如果是列表/字典等复杂类型，跳过
                if isinstance(raw_id, (list, dict)):
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}] 跳过非法 camera_id 类型: {type(raw_id).__name__}, 值: {raw_id!r}")
                    continue
                cam_id_str = str(raw_id).strip()
                if not cam_id_str or cam_id_str in ('[]', 'None', ''):
                    continue
                camera_ids.append(cam_id_str)

            # 防御性检查：如果没有有效的摄像机 ID，跳过检测器池注册
            if not camera_ids:
                logger.debug(f"[MultiCameraExecutor-{self.task_id}] 没有关联的摄像机，跳过检测器池注册")
                # ✅ 优雅退出：不抛出RuntimeError，直接返回让调用者处理
                return False

            # 构造 detector_config dict（用于 LocalObjectDetector）
            # 模型输入尺寸优先级：任务 detector_config（create_task 时从算法库写入）> 算法库 input_size > 640 兜底
            input_width = detector_config_block.get('input_width')
            input_height = detector_config_block.get('input_height')
            if not input_width or not input_height:
                # 兼容旧任务：回查算法库 input_size（如 "640x640"）
                input_size_str = algorithm_config.get('input_size', '640x640')
                try:
                    # 兼容 '640x640' 和 '640*640' 两种分隔符写法
                    parts = input_size_str.lower().replace('*', 'x').split('x')
                    if len(parts) == 2:
                        input_width = int(parts[0])
                        input_height = int(parts[1])
                except Exception:
                    pass
            # 最终兜底（算法库缺失或解析失败时）
            input_width = input_width or 640
            input_height = input_height or 640

            detector_config_dict = {
                'model_path': detector_config['model_path'],
                'model_type': factory_algorithm,
                'backend': detector_config['backend'],
                'conf_threshold': detector_config['conf_threshold'],
                'iou_threshold': detector_config['iou_threshold'],
                'min_box_area': detector_config['min_box_area'],
                'use_fp16': detector_config['use_fp16'],
                'device_id': detector_config['device_id'],
                'gpu_mem_limit': detector_config['gpu_mem_limit'],
                'tensorrt_lib_path': detector_config['tensorrt_lib_path'],
                'tensorrt_engine_path': detector_config['tensorrt_engine_path'],
                'rknn_target': detector_config['rknn_target'],
                'rknn_device_id': detector_config['rknn_device_id'],
                'rknn_core_mask': detector_config['rknn_core_mask'],
                'input_width': input_width,
                'input_height': input_height,
            }

            # 检测器进程数：优先用 detector_config 中的配置，否则按摄像机数自动计算
            num_detectors = detector_config_block.get('num_detectors')
            if num_detectors is None:
                num_detectors = max(2, min(16, (len(camera_ids) + 3) // 4))
                logger.info(f"[MultiCameraExecutor-{self.task_id}] 检测器数量自动计算: "
                            f"{len(camera_ids)}路摄像机 → {num_detectors}个检测器")
            else:
                logger.info(f"[MultiCameraExecutor-{self.task_id}] 检测器数量(手动配置): {num_detectors}")

            # 多进程共享 GPU 时强制取消 per-process gpu_mem_limit
            # gpu_mem_limit 是每进程上限，N进程 × 6GB 在 8GB 显卡上会互相抢占导致 CUDA handle 损坏
            if num_detectors > 1 and detector_config_dict.get('gpu_mem_limit'):
                logger.info(f"[MultiCameraExecutor-{self.task_id}] 多进程({num_detectors})场景, "
                            f"取消 per-process gpu_mem_limit={detector_config_dict['gpu_mem_limit']}, "
                            f"改由 ONNX 动态管理显存")
                detector_config_dict['gpu_mem_limit'] = 0

            input_shape = (1, input_height, input_width, 3)

            # 注册任务（创建共享内存 + RemoteObjectDetector）
            # 注意：返回的 RemoteObjectDetector 仅用于主进程持有引用（防止 GC），
            # 子进程会用相同参数重建。
            self.detector_pool_manager.register_task(
                task_id=str(self.task_id),
                camera_ids=camera_ids,
                model_type=factory_algorithm,
                detector_config=detector_config_dict,
                class_names=list(class_names),
                num_detectors=num_detectors,
                input_shape=input_shape,
            )

            # 保存每个相机的 detector_params 和 out_event（传给子进程用）
            pool = self.detector_pool_manager.pools[factory_algorithm]
            for cam_id in camera_ids:
                self.detector_params_by_camera[cam_id] = {
                    'camera_id': cam_id,
                    'model_type': factory_algorithm,
                    'class_names': list(class_names),
                    'input_shape': input_shape,
                    'input_height': input_height,
                    'input_width': input_width,
                }
                self.out_events[cam_id] = pool.out_events[cam_id]

            logger.debug(f"[MultiCameraExecutor-{self.task_id}] 检测器进程池初始化成功")
            logger.debug(f"[MultiCameraExecutor-{self.task_id}]   模型类型: {factory_algorithm}")
            logger.debug(f"[MultiCameraExecutor-{self.task_id}]   检测器数量: {num_detectors}")
            logger.debug(f"[MultiCameraExecutor-{self.task_id}]   输入尺寸: {input_width}x{input_height}")
            logger.debug(f"[MultiCameraExecutor-{self.task_id}]   摄像机数量: {len(camera_ids)}")
            logger.debug(f"[MultiCameraExecutor-{self.task_id}]   后端: {detector_config['backend']}")
            logger.debug(f"[MultiCameraExecutor-{self.task_id}]   FP16: {detector_config['use_fp16']}")

            # ========================================
            # 步骤6：创建 detected_objects_queue 和 stop_event
            # ========================================
            self.detected_objects_queue = multiprocessing.Queue(maxsize=2000)
            self.stop_event = multiprocessing.Event()
            # processor 就绪事件：子进程完成初始化后 set，
            # 主进程等待此事件后再启动 CameraWorker，避免队列在 processor 就绪前被填满
            self.processor_ready_event = multiprocessing.Event()

            # ========================================
            # 步骤7：构建 camera_configs（给 TrackedObjectProcessor 用）
            # ========================================
            camera_configs_for_processor = {}
            decoder_cfg = self.task_config.get('decoder_config', {})

            # 事件规则去重配置（任务级，独立于大模型，不消耗 token）
            # task_config 已含 dedup_enabled/dedup_window_seconds/dedup_iou_threshold，
            # 未配置时回退到系统配置 event_dedup 段默认值
            try:
                from src.system.system_config_loader import get_event_dedup_config
                _sys_dedup = get_event_dedup_config()
            except Exception:
                _sys_dedup = {'enabled': True, 'window_seconds': 300, 'iou_threshold': 0.85}
            _dedup_cfg = {
                'enabled': self.task_config.get('dedup_enabled', _sys_dedup.get('enabled', True)),
                'window_seconds': self.task_config.get('dedup_window_seconds', _sys_dedup.get('window_seconds', 300)),
                'iou_threshold': self.task_config.get('dedup_iou_threshold', _sys_dedup.get('iou_threshold', 0.85)),
            }

            for cam in self.cameras:
                cam_id = cam['camera_id']
                # 人员统计：读取本相机（通道）关联的本任务计数区域，并标注相机在
                # direction 模式下的角色（in/out）；line 模式仅当通道匹配时携带。
                counting_zones_for_cam = []
                if self.task_config.get('detection_type') == 'people_counting':
                    counting_zones_for_cam = self._load_counting_zones_for_camera(cam)
                camera_configs_for_processor[cam_id] = {
                    'frame_shape': (
                        decoder_cfg.get('height', 640),
                        decoder_cfg.get('width', 640)
                    ),
                    'threshold': detector_config['conf_threshold'],
                    'stationary_threshold': self.task_config.get('stationary_threshold', 10),
                    'zones_config': cam.get('zones_config', []),  # 通道级 Zone 配置
                    'event_config': self.task_config.get('event_config', {}),
                    'task_id': str(self.task_id),
                    'algorithm': self.algorithm,
                    'class_names': list(class_names),
                    'fps': decoder_cfg.get('fps', 25),
                    'dedup_config': _dedup_cfg,  # 事件规则去重配置（任务级）
                    'detection_type': self.task_config.get('detection_type', 'realtime'),
                    'event_type': self.task_config.get('event_type', 'tracking'),  # 语义检测类型（事件类型）
                    'counting_zones': counting_zones_for_cam,
                }

            # ========================================
            # 步骤8：构建 AI 流配置（端口从 system_config.yaml 读取）
            # ========================================
            ai_stream_config = self._build_ai_stream_config()

            # ========================================
            # 步骤9：创建 TrackedObjectProcessorPool（多进程并行处理事件）
            # 单进程处理 16 路高帧率相机时吞吐量不足，改为多进程：
            # 每 4 个相机 1 个 worker 进程（最少 1 最多 8），分发线程按 camera_id 路由
            # ========================================
            from src.events.tracked_object_processor import TrackedObjectProcessorPool

            self.tracked_object_processor = TrackedObjectProcessorPool(
                camera_configs=camera_configs_for_processor,
                detected_objects_queue=self.detected_objects_queue,
                stop_event=self.stop_event,
                ai_stream_config=ai_stream_config,
                ai_stream_enabled_state=self.ai_stream_enabled_state,
                ptz_metrics=self.ptz_metrics if self.ptz_metrics else None,
            )

            logger.debug(f"[MultiCameraExecutor-{self.task_id}] TrackedObjectProcessorPool 初始化完成")

            return True

        except Exception as e:
            logger.debug(f"[MultiCameraExecutor-{self.task_id}] 共享组件初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _load_counting_zones_for_camera(self, cam: Dict) -> list:
        """人员统计：读取本相机（通道）关联的本任务计数区域并标注角色

        委托 task_manager.get_counting_zones(task_id, channel_id)：
        - line 模式：channel_id 匹配才返回，role='line'
        - direction 模式：in/out_channels 含本通道时返回，role='in'/'out'
        返回 row 字典列表，供 TrackedObjectProcessor 构建 CountingZone。
        """
        try:
            channel_id = cam.get('channel_id')
            if not channel_id:
                return []
            from src.tasks.task_manager import TaskManager
            tm = TaskManager()
            zones = tm.get_counting_zones(self.task_id, int(channel_id))
            # direction 模式：zone.channel_id 在表内为 null，
            # 补充为本相机（通道）的 channel_id，保证计数归属到正确的通道明细
            for z in zones:
                if z.get('mode') == 'direction':
                    z['channel_id'] = int(channel_id)
            return zones
        except Exception as e:
            logger.error(f"[TaskExecutor] 加载计数区域失败 channel={cam.get('channel_id')}: {e}")
            return []

    def _build_ai_stream_config(self) -> dict:
        """
        构建 AI 视频流推送配置

        端口从 system_config.yaml 读取（项目约束：不硬编码端口）
        flag_dir 指向主进程创建的标记文件目录，供 TrackedObjectProcessor
        以文件 I/O 方式检测某摄像机是否启用 AI 流（替代 Manager dict 代理）。
        """
        try:
            from src.system.system_config_loader import get_web_server_host, get_web_server_port
            host = get_web_server_host('127.0.0.1')
            port = get_web_server_port()

            # 显示配置（框颜色、线宽等）
            display_config = self.task_config.get('display_config', {})
            display_cfg = display_config.get('display', {})

            # AI 流标记目录（与 run.py._ai_stream_flag_dir 保持一致）
            flag_dir = str(Path(project_root) / 'data' / 'ai_stream_flags')

            ai_stream_config = {
                'interval': display_cfg.get('ai_stream_interval', 2),
                'api_url': f'http://{host}:{port}/api/ai-stream/push',
                'display_config': display_config,
                'flag_dir': flag_dir,
            }

            logger.debug(f"[MultiCameraExecutor-{self.task_id}] AI 流配置: {ai_stream_config['api_url']}, flag_dir={flag_dir}")
            return ai_stream_config

        except Exception as e:
            logger.debug(f"[MultiCameraExecutor-{self.task_id}] AI 流配置构建失败，使用默认值: {e}")
            # 兜底通过集中访问器读取 host/port（单一真相源，避免硬编码）
            from src.system.system_config_loader import get_web_server_host, get_web_server_port
            _host = get_web_server_host('127.0.0.1')
            _port = get_web_server_port()
            return {
                'interval': 2,
                'api_url': f'http://{_host}:{_port}/api/ai-stream/push',
                'display_config': {},
                'flag_dir': str(Path(project_root) / 'data' / 'ai_stream_flags'),
            }

    def start(self):
        """启动多摄像机检测"""
        self.running = True

        # 初始化共享组件
        if not self.init_shared_components():
            logger.debug(f"[MultiCameraExecutor-{self.task_id}] 初始化失败，无法启动")
            return False

        # 1. 启动所有检测器进程（必须先于 CameraWorker，确保共享内存已就绪）
        self.detector_pool_manager.start_all()

        # 等待检测器进程就绪（模型加载+SHM连接完成）
        # 不等待的话 CameraWorker 启动后检测全部超时，帧被环形缓冲区覆盖
        logger.info(f"[MultiCameraExecutor-{self.task_id}] 等待检测器进程加载模型...")
        _wait_start = time.time()
        if not self.detector_pool_manager.wait_ready(timeout=120):
            logger.warning(
                f"[MultiCameraExecutor-{self.task_id}] "
                f"等待检测器就绪超时(120s)，部分检测器可能未就绪，继续启动"
            )
        else:
            logger.info(
                f"[MultiCameraExecutor-{self.task_id}] "
                f"所有检测器已就绪（模型加载+SHM连接完成），耗时 {time.time()-_wait_start:.2f}s"
            )

        # 2. 启动 TrackedObjectProcessorPool（多进程并行处理事件）
        _proc_start = time.time()
        self.tracked_object_processor.start()
        logger.info(
            f"[MultiCameraExecutor-{self.task_id}] "
            f"TrackedObjectProcessorPool 已启动 (workers={self.tracked_object_processor.num_workers})"
        )

        # 等待所有 worker 完成初始化（CameraState/EventOutput/FrameCache/线程池就绪）
        # 再启动 CameraWorker，避免 CameraWorker 投递的帧在 worker 就绪前填满队列
        if not self.tracked_object_processor.wait_ready(timeout=60):
            logger.warning(
                f"[MultiCameraExecutor-{self.task_id}] "
                f"等待 TrackedObjectProcessorPool 就绪超时(60s)，继续启动 CameraWorker"
            )
        else:
            logger.info(
                f"[MultiCameraExecutor-{self.task_id}] "
                f"TrackedObjectProcessorPool 所有 worker 已就绪，耗时 {time.time()-_proc_start:.2f}s，开始启动 CameraWorker"
            )

        # 3. 为每个摄像机创建工作进程
        for camera in self.cameras:
            camera_id = camera.get('camera_id')
            model_type = self.detector_params_by_camera[camera_id]['model_type']

            process = Process(
                target=_camera_worker_process_wrapper,
                args=(
                    camera,
                    self.task_config,
                    self.detector_params_by_camera[camera_id],
                    self.detector_pool_manager.pools[model_type].detection_queue,
                    self.out_events[camera_id],
                    self.detected_objects_queue,
                    self.stop_event,
                    self.ptz_metrics.get(camera_id) if self.ptz_metrics else None,
                ),
                name=f"CameraWorker-{camera_id}",
                daemon=True
            )
            process.start()
            self.worker_processes[camera_id] = process

            # 设置 CPU 亲和性：核心 0-1 留给系统/Flask/TrackedObjectProcessor/DetectorPool，
            # 其余核心分配给 CameraWorker（每进程 2 核，超出范围则降级到不绑定）
            try:
                import psutil
                p = psutil.Process(process.pid)
                total_cores = psutil.cpu_count(logical=False) or 8
                reserved_cores = 2  # 核心 0-1 保留给系统及非 CameraWorker 进程
                worker_index = len(self.worker_processes) - 1
                core_start = reserved_cores + worker_index * 2
                core_end = core_start + 2
                available_cores = list(range(core_start, min(core_end, total_cores)))
                if available_cores:
                    p.cpu_affinity(available_cores)
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}] CameraWorker-{camera_id} 绑定到核心 {available_cores}")
                else:
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}] CameraWorker-{camera_id} 无可用独占核心（core_start={core_start}, total={total_cores}），不绑定亲和性")
            except Exception as e:
                logger.debug(f"[MultiCameraExecutor-{self.task_id}] 设置 CPU 亲和性失败: {e}")

            logger.debug(f"[MultiCameraExecutor-{self.task_id}] 启动摄像机工作进程: {camera_id}")

        logger.debug(f"[MultiCameraExecutor-{self.task_id}] 所有摄像机已启动")
        return True

    def run(self):
        """运行任务执行器（主循环）"""
        if not self.start():
            logger.debug(f"[MultiCameraExecutor-{self.task_id}] 启动失败，退出")
            return

        logger.debug(f"[MultiCameraExecutor-{self.task_id}] 进入主循环")

        try:
            while self.running:
                # 检查所有工作进程是否存活
                alive_processes = 0
                for camera_id, process in self.worker_processes.items():
                    if process.is_alive():
                        alive_processes += 1
                    else:
                        logger.debug(f"[MultiCameraExecutor-{self.task_id}] 摄像机工作进程 {camera_id} 已停止")

                # 检查 TrackedObjectProcessor 是否存活
                if self.tracked_object_processor and not self.tracked_object_processor.is_alive():
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}] TrackedObjectProcessor 已停止，退出")
                    self.running = False
                    break

                # 如果所有进程都停止，退出主循环
                if alive_processes == 0:
                    if self.is_batch:
                        # 批处理：所有工作进程已处理完文件，等待事件队列排空再退出，
                        # 避免 TrackedObjectProcessor 尚未消费完事件就被停止而丢失
                        logger.debug(f"[MultiCameraExecutor-{self.task_id}] 批处理工作进程已结束，等待事件队列排空...")
                        self._wait_batch_drain()
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}] 所有工作进程已停止，退出主循环")
                    self.running = False
                    break

                # 每5秒检查一次进程状态
                time.sleep(5)

        except KeyboardInterrupt:
            logger.debug(f"[MultiCameraExecutor-{self.task_id}] 收到中断信号，停止任务")
            self.stop(reason="keyboard_interrupt")
        except Exception as e:
            logger.debug(f"[MultiCameraExecutor-{self.task_id}] 主循环异常: {e}")
            self.stop(reason=f"run_loop_error: {e}")

        logger.debug(f"[MultiCameraExecutor-{self.task_id}] 任务执行器已退出")

    def _wait_batch_drain(self, timeout: float = 30.0):
        """批处理结束：等待事件队列排空与事件处理完成（带超时）

        批处理工作进程处理完文件后退出，但 detected_objects_queue 中可能仍有
        待 TrackedObjectProcessor 消费的事件。直接停止会丢失事件，故先等待队列
        排空，再给事件处理（LLM/GIF/MQTT 异步）额外的 settle 时间。
        """
        deadline = time.time() + timeout
        queue = self.detected_objects_queue

        # 1. 等待检测结果队列排空
        while time.time() < deadline:
            try:
                size = queue.qsize()
                if size <= 0:
                    break
            except Exception:
                break  # multiprocessing.Queue.qsize 部分平台不可靠，退化为固定等待
            time.sleep(0.5)

        # 2. 事件处理（LLM/GIF/MQTT 异步）额外 settle 时间
        time.sleep(3.0)
        logger.debug(f"[MultiCameraExecutor-{self.task_id}] 批处理事件排空等待完成")

    def stop(self, reason: str = "unknown"):
        """停止所有摄像机检测

        Args:
            reason: 停止原因（用于日志定位静默停止问题，如 manual /
                    keyboard_interrupt / run_loop_error / stop_all / process_manager 等）
        """
        logger.info(f"[MultiCameraExecutor-{self.task_id}] 停止任务，原因: {reason}")
        self.running = False

        # 1. 通知所有进程退出
        if self.stop_event:
            self.stop_event.set()

        # 2. 等待 CameraWorker 进程退出
        for camera_id, process in self.worker_processes.items():
            if process.is_alive():
                process.join(timeout=5)
                if process.is_alive():
                    logger.debug(f"[MultiCameraExecutor-{self.task_id}] 终止摄像机工作进程: {camera_id}")
                    process.terminate()

        self.worker_processes.clear()

        # 3. 停止 TrackedObjectProcessor
        if self.tracked_object_processor and self.tracked_object_processor.is_alive():
            self.tracked_object_processor.join(timeout=5)
            if self.tracked_object_processor.is_alive():
                logger.debug(f"[MultiCameraExecutor-{self.task_id}] 终止 TrackedObjectProcessor")
                self.tracked_object_processor.terminate()

        # 4. 停止检测器进程池（清理所有共享内存）
        if self.detector_pool_manager:
            self.detector_pool_manager.stop()

        logger.debug(f"[MultiCameraExecutor-{self.task_id}] 已停止")

    def get_status(self) -> Dict:
        """获取执行器状态"""
        camera_status = {}
        for camera_id, process in self.worker_processes.items():
            camera_status[camera_id] = {
                'running': process.is_alive(),
                'process_id': process.pid if process.is_alive() else None
            }

        return {
            'task_id': self.task_id,
            'task_name': self.task_name,
            'running': self.running,
            'cameras': camera_status,
            'tracked_object_processor_running': (
                self.tracked_object_processor.is_alive()
                if self.tracked_object_processor else False
            ),
        }


class TaskScheduler:
    """任务调度器 - 管理多个并行任务"""

    def __init__(self):
        """初始化任务调度器"""
        self.executors: Dict[int, MultiCameraTaskExecutor] = {}
        self.running = False
        self._lock = threading.Lock()

        logger.debug("[TaskScheduler] 初始化")

    def start_task(self, task_id: int, task_config: Dict, cameras: List[Dict]) -> bool:
        """启动任务

        Args:
            task_id: 任务 ID
            task_config: 任务配置
            cameras: 摄像机列表

        Returns:
            是否启动成功
        """
        with self._lock:
            if task_id in self.executors:
                logger.debug(f"[TaskScheduler] 任务 {task_id} 已在运行")
                return False

            executor = MultiCameraTaskExecutor(task_config, cameras)

            if executor.start():
                self.executors[task_id] = executor
                logger.debug(f"[TaskScheduler] 任务 {task_id} 启动成功")
                return True
            else:
                logger.debug(f"[TaskScheduler] 任务 {task_id} 启动失败")
                return False

    def stop_task(self, task_id: int) -> bool:
        """停止任务"""
        with self._lock:
            if task_id not in self.executors:
                logger.debug(f"[TaskScheduler] 任务 {task_id} 未运行")
                return False

            executor = self.executors[task_id]
            executor.stop(reason="manual")
            del self.executors[task_id]

            logger.debug(f"[TaskScheduler] 任务 {task_id} 已停止")
            return True

    def stop_all(self):
        """停止所有任务"""
        with self._lock:
            for task_id, executor in self.executors.items():
                executor.stop(reason="stop_all")

            self.executors.clear()
            logger.debug("[TaskScheduler] 所有任务已停止")

    def get_running_tasks(self) -> List[Dict]:
        """获取运行中的任务状态"""
        with self._lock:
            return [executor.get_status() for executor in self.executors.values()]

    def get_task_status(self, task_id: int) -> Optional[Dict]:
        """获取单个任务状态"""
        with self._lock:
            if task_id in self.executors:
                return self.executors[task_id].get_status()
            return None


# 全局任务调度器实例
_task_scheduler = None


def get_task_scheduler() -> TaskScheduler:
    """获取任务调度器实例"""
    global _task_scheduler
    if _task_scheduler is None:
        _task_scheduler = TaskScheduler()
    return _task_scheduler


# ========================================
# 主入口（兼容 task_worker.py 的命令行接口）
# ========================================

def main():
    """任务执行器入口（兼容单任务模式）"""
    from logging.handlers import RotatingFileHandler

    try:
        # 加载系统配置
        config_path = Path(project_root) / 'config' / 'system_config.yaml'
        with open(config_path, 'r', encoding='utf-8') as f:
            full_cfg = yaml.safe_load(f) or {}
        log_cfg = full_cfg.get('logging', {})

        # 日志参数
        log_level = getattr(logging, log_cfg.get('level', 'INFO').upper(), logging.INFO)
        log_format = log_cfg.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        log_file = log_cfg.get('file', 'logs/system.log')
        log_max_size = int(log_cfg.get('max_size', 10)) * 1024 * 1024
        log_backup_count = int(log_cfg.get('backup_count', 5))

        # 确保日志目录存在
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 配置根 logger
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.handlers.clear()

        # 文件 handler（带轮转）
        file_handler = RotatingFileHandler(
            log_file, maxBytes=log_max_size, backupCount=log_backup_count, encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(file_handler)

        # 控制台 handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(console_handler)

        logger.debug(f"[TaskExecutor] 子进程日志配置完成: {log_file}")
    except Exception as e:
        logger.debug(f"[TaskExecutor] 日志配置失败: {e}")

    if len(sys.argv) < 2:
        logger.debug("Usage: python -m src.tasks.task_executor <task_config_json>")
        sys.exit(1)

    # 解析任务配置
    try:
        task_config = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        logger.debug(f"[TaskExecutor] 配置解析失败: {e}")
        sys.exit(1)

    # 单任务模式（兼容 task_worker.py）
    camera_id = task_config.get('camera_id', 'unknown')
    rtsp_url = task_config.get('rtsp_url', '')

    cameras = [
        {
            'camera_id': camera_id,
            'rtsp_url': rtsp_url
        }
    ]

    # 创建并运行任务
    executor = MultiCameraTaskExecutor(task_config, cameras)
    executor.start()

    # 等待信号
    try:
        signal.signal(signal.SIGINT, lambda signum, frame: executor.stop(reason="sigint"))
        signal.signal(signal.SIGTERM, lambda signum, frame: executor.stop(reason="sigterm"))

        while executor.running:
            time.sleep(1)

    except KeyboardInterrupt:
        executor.stop(reason="keyboard_interrupt")


if __name__ == '__main__':
    main()
