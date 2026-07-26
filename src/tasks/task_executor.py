"""
多摄像机任务执行器（实时检测）

支持一个检测任务同时检测多个摄像机。
每个摄像机使用独立的解码器和跟踪器，共享同一个检测器（算法模型）。
所有配置从数据库读取，无需依赖配置文件。

架构特性：
- 多进程架构：每个摄像机一个CameraWorker子进程
- 共享检测器：全局检测进程池，所有摄像机共享
- 零拷贝传输：环形缓冲区共享内存传递视频帧
- 运动检测：支持运动触发检测模式（节省GPU资源）
- RGB帧复用：检测结果包含RGB帧，避免重复预处理
"""

import sys
import os
import time
import json
import threading
import multiprocessing
from multiprocessing import Process, Queue
import queue
import cv2
import numpy as np
from collections import deque
from datetime import datetime
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor
import logging

# ✅ 配置日志
logger = logging.getLogger(__name__)

# ✅ 全局锁：保护 OpenCV 操作的线程安全
# OpenCV 在多线程环境中可能使用全局缓冲区，导致数据混淆
_opencv_lock = threading.Lock()

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)


def _camera_worker_process_wrapper(camera_config, task_config, detection_queue, result_queues):
    """
    摄像机工作进程包装函数（用于多进程架构）

    在子进程中创建并运行CameraWorker，避免pickle序列化问题

    Args:
        camera_config: 摄像机配置（可序列化）
        task_config: 任务配置（可序列化）
        detection_queue: 检测队列（multiprocessing.Queue，可序列化）
        result_queues: 结果队列字典（{camera_id: result_queue}）或单个队列（向后兼容）
    """
    try:
        # 在子进程中动态导入跟踪器类（避免序列化问题）
        from src.tracking.botsort import BoTSORTTracker

        # ✅ 关键修复：从result_queues中获取当前摄像机的队列
        camera_id = camera_config.get('camera_id')
        if isinstance(result_queues, dict):
            # 新架构：从字典中获取当前摄像机的队列
            result_queue = result_queues.get(camera_id)
            if result_queue is None:
                print(f"[CameraWorker-{camera_id}] [ERROR] 无法找到摄像机对应的队列")
                return
        else:
            # 向后兼容：直接使用传入的队列
            result_queue = result_queues

        # 在子进程中创建CameraWorker实例
        worker = CameraWorker(
            camera_config=camera_config,
            task_config=task_config,
            tracker_class=BoTSORTTracker,
            detection_queue=detection_queue,
            result_queue=result_queue
        )
        # 运行工作循环
        worker.run()
    except Exception as e:
        print(f"[CameraWorker进程] 异常退出: {e}")
        import traceback
        traceback.print_exc()


class CameraWorker:
    """单个摄像机的检测工作进程"""
    
    def __init__(self, camera_config: Dict, task_config: Dict, tracker_class, detection_queue=None, result_queue=None):
        """
        初始化摄像机工作进程（全局检测池模式）

        Args:
            camera_config: 摄像机配置
            task_config: 任务配置（从数据库读取的完整配置）
            tracker_class: 跟踪器类
            detection_queue: 全局检测进程池的任务队列
            result_queue: 全局检测进程池的结果队列
        """
        self.camera_config = camera_config
        self.task_config = task_config
        self.tracker_class = tracker_class
        self.detection_queue = detection_queue  # 全局检测进程池的任务队列
        self.result_queue = result_queue  # 全局检测进程池的结果队列

        # ========================================
        # 全局检测池模式（唯一模式）
        # ========================================
        self.latest_detections = []  # 最新检测结果缓存
        
        self.camera_id = camera_config.get('camera_id', 'unknown')
        self.rtsp_url = camera_config.get('rtsp_url', '')
        self.task_id = task_config.get('id')
        self.task_name = task_config.get('task_name', 'unknown')
        self.algorithm = task_config.get('algorithm', 'yolov11')
        self.running = False
        
        # 摄像机专属组件
        self.decoder = None
        self.tracker = None
        # ✅ 删除：self.preprocessor（检测器进程已处理预处理）
        # ✅ 删除：self.detector_cfg（CameraWorker不需要检测器配置）
        self.motion_detector = None
        self.event_output = None
        self.gif_generator = None

        # 从配置中提取各模块配置
        self.decoder_cfg = task_config.get('decoder_config', {})
        # ✅ 删除：self.detector_cfg（检测器进程已处理）
        self.tracker_cfg = task_config.get('tracker_config', {})
        self.motion_cfg = task_config.get('motion_config', {})
        self.event_cfg = task_config.get('event_config', {})
        self.display_cfg = task_config.get('display_config', {})
        
        # 提取显示子配置
        self._display = self.display_cfg.get('display', {})
        self._det_box_cfg = self.display_cfg.get('detection_box', {})
        self._track_box_cfg = self.display_cfg.get('track_box', {})
        self._trajectory_cfg = self.display_cfg.get('trajectory', {})
        self._motion_region_cfg = self.display_cfg.get('motion_region', {})
        self._debug_info_cfg = self.display_cfg.get('debug_info', {})
        
        # 跟踪帧序列收集器（用于生成 GIF）
        self.track_frame_sequences = {}  # {track_id: [frame_data]}
        
        # 轨迹数据收集器（用于发送轨迹数据）
        self.track_trajectories = {}  # {track_id: {'points': [], 'start_time': None, 'class_name': ''}}
        
        # 活动跟踪目标记录（用于判断首次出现/消失）
        self.active_tracks = {}  # {track_id: {'first_frame': id, 'last_bbox': [], 'last_update': time}}
        
        # 已处理事件的 track_id 集合（防止同一目标重复产生事件）
        self.processed_track_ids = set()
        
        # GIF 输出配置（从 event_config.gif 读取）
        self.gif_cfg = self.event_cfg.get('gif', {})
        self.generate_gif = self.gif_cfg.get('enabled', True) and self.gif_cfg.get('output_method', 'none') != 'none'  # 同时检查 enabled 开关和输出方法
        self.gif_max_frames = self.gif_cfg.get('max_frames', 30)  # GIF 最大帧数
        self.gif_fps = self.gif_cfg.get('fps', 5)  # GIF 帧率
        self.gif_max_width = self.gif_cfg.get('max_width', 320)  # GIF 最大宽度
        self.gif_max_height = self.gif_cfg.get('max_height', 240)  # GIF 最大高度
        self.gif_output_dir = self.gif_cfg.get('output_dir', 'output/gifs')  # GIF 输出目录
        self.gif_draw_trajectory = self.gif_cfg.get('draw_trajectory', True)  # 是否绘制轨迹
        
        # ========================================
        # 帧缓存（用于GIF生成：覆盖事件前5秒+事件后5秒的原始完整帧）
        # ========================================
        self.frame_cache = deque()  # [(time_sec, frame_id, frame_small)]
        self.frame_cache_fps = self.gif_cfg.get('cache_fps', 5)  # 缓存采样率（从10降到5，减少内存占用）
        self.frame_cache_retention = float(self.gif_cfg.get('cache_retention', 10.0))  # 缓存保留秒数（从20秒降到10秒）
        self.frame_cache_last_ts = 0.0  # 上次缓存时间

        # ========================================
        # 新增：原始视频流尺寸（从 ZLM 获取）
        # ========================================
        # 用于坐标映射：检测框（640x640）-> 原始尺寸
        self.zlm_original_width = 0  # ZLM 原始流宽度
        self.zlm_original_height = 0  # ZLM 原始流高度

        # ========================================
        # 新增：视频文件原始分辨率（用于AI流还原）
        # ========================================
        # 视频文件解码后缩放到640x640，推送AI流时需要还原到原始分辨率
        self.source_original_width = 0  # 视频文件原始宽度
        self.source_original_height = 0  # 视频文件原始高度

        # 兼容旧代码（GIF生成使用）
        self.frame_orig_w = 0  # 原始帧宽（用于bbox缩放）
        self.frame_orig_h = 0  # 原始帧高
        
        # 待生成的GIF事件（目标消失后等待 post_seconds 再生成，以捕获"结束后5秒"帧）
        self.pending_gifs = []  # [{track_id, start_time, end_time, generate_time, trajectory, frame_seq}]
        self.gif_pre_seconds = float(self.gif_cfg.get('pre_seconds', 5.0))  # 事件起始前多少秒
        self.gif_post_seconds = float(self.gif_cfg.get('post_seconds', 5.0))  # 事件结束后多少秒
        
        # 轨迹数据配置
        self.trajectory_cfg = self.event_cfg.get('trajectory', {})
        self.enable_trajectory = self.trajectory_cfg.get('enabled', True)  # 是否启用轨迹数据
        self.trajectory_send_mode = self.trajectory_cfg.get('send_mode', 'complete')  # realtime | complete | both
        self.trajectory_max_points = self.trajectory_cfg.get('max_points', 100)  # 最大轨迹点数
        self.trajectory_send_interval = self.trajectory_cfg.get('send_interval', 10)  # 实时发送间隔（帧）
        
        # 关键帧采集配置（从 event_config.capture 读取）
        self.capture_cfg = self.event_cfg.get('capture', {})
        self.capture_strategy = self.capture_cfg.get('strategy', 'keyframe')  # full | cropped | keyframe | periodic
        self.capture_interval = self.capture_cfg.get('interval', 10)  # 周期性采集间隔
        self.change_threshold = self.capture_cfg.get('change_threshold', 50)  # 变化阈值（像素）
        self.save_full_on_start = self.capture_cfg.get('save_full_on_start', True)  # 首次出现保存完整帧
        self.save_full_on_end = self.capture_cfg.get('save_full_on_end', True)  # 目标消失保存完整帧
        # 裁剪边距比例：在 bbox 各方向扩展 bbox 尺寸的比例（0.5=50%, 1.0=100%）
        # 值越大，裁剪画面范围越大，目标在画面中占比越小，保留更多上下文
        self.crop_margin_ratio = self.capture_cfg.get('crop_margin_ratio', 1.0)
        
        # 事件图片/GIF 是否绘制检测框（前端开关控制）
        image_output_cfg = self.event_cfg.get('image_output', {})
        self.event_image_draw_bbox = image_output_cfg.get('draw_bbox', True)
        self.gif_draw_bbox = self.gif_cfg.get('draw_bbox', True)
        
        # 统计
        self.frame_count = 0
        self.fps = 0.0
        self.last_fps_time = time.time()
        
        # AI 视频流 WebSocket 配置
        self.ai_stream_enabled = self._display.get('ai_stream_enabled', False)
        self.ai_stream_interval = self._display.get('ai_stream_interval', 2)
        self.ai_stream_frame_counter = 0
        
        # AI 流异步处理队列和线程
        self.ai_stream_queue = queue.Queue(maxsize=10)  # 增大队列减少丢帧
        self.ai_stream_thread = None
        self.ai_stream_stop_event = threading.Event()
        
        # ========================================
        # GIF生成线程池（限制并发，避免线程泄漏）
        # ========================================
        # 说明：GIF生成是耗时IO操作（编码+上传），使用线程池限制并发数
        # - max_workers=2：最多同时生成2个GIF，避免CPU占用过高
        # - 防止线程泄漏：每次GIF生成不再创建新线程，而是提交到线程池
        self.gif_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f'GIF-{self.camera_id}')
        
        # ========================================
        # 新增：Zone 区域管理（借鉴 Frigate）
        # ========================================
        # 功能：区域检测、事件过滤、入侵检测
        self.zone_manager = None  # 延迟初始化（需要读取 Zone 配置）
        self.zones_config = task_config.get('zones_config', [])  # Zone 配置列表
        
        # ========================================
        # 新增：对象状态管理（借鉴 Frigate）
        # ========================================
        # 功能：静止/移动状态判断、推理优化
        from src.detection.object_state_manager import ObjectStateManager
        self.object_state_manager = ObjectStateManager(
            position_change_threshold=task_config.get('position_change_threshold', 50.0),
            min_history_length=task_config.get('min_history_length', 10),
            stationary_skip_frames=task_config.get('stationary_skip_frames', 5)
        )
        
        print(f"[CameraWorker-{self.camera_id}] 初始化")
        print(f"[CameraWorker-{self.camera_id}] RTSP: {self.rtsp_url}")
        print(f"[CameraWorker-{self.camera_id}] 任务名称: {self.task_name}")
        print(f"[CameraWorker-{self.camera_id}] 算法: {self.algorithm}")
        print(f"[CameraWorker-{self.camera_id}] 目标类别: {task_config.get('target_classes', [])}")
        print(f"[CameraWorker-{self.camera_id}] GIF输出方法: {self.gif_cfg.get('output_method', 'none')}")
        print(f"[CameraWorker-{self.camera_id}] GIF生成: {self.generate_gif}")
        print(f"[CameraWorker-{self.camera_id}] GIF线程池: 2个并发")
        print(f"[CameraWorker-{self.camera_id}] Zone 区域: {len(self.zones_config)} 个")
        print(f"[CameraWorker-{self.camera_id}] 对象状态管理: 已启用")
    
    def init_components(self):
        """初始化摄像机专属组件"""
        try:
            # ========================================
            # 重要：检查摄像机类型（视频文件跳过ZLM推流）
            # ========================================
            camera_protocol = self.camera_config.get('protocol', 'rtsp')
            is_video_file = camera_protocol == 'file'

            if is_video_file:
                print(f"[CameraWorker-{self.camera_id}] 视频文件模式，跳过ZLM推流检查")
                
                # ========================================
                # 新增：获取视频文件原始分辨率（用于AI流还原）
                # ========================================
                try:
                    from src.utils.video_utils import get_video_original_resolution

                    # 从摄像机配置获取视频文件路径
                    # 注意：视频文件路径存储在 self.rtsp_url 中（第369行）
                    video_file = self.camera_config.get('video_file', '') or self.rtsp_url

                    # 去除 file:// 前缀（如果有）
                    if video_file.startswith('file://'):
                        video_file = video_file[7:]

                    if video_file:
                        self.source_original_width, self.source_original_height = get_video_original_resolution(video_file)

                        if self.source_original_width > 0 and self.source_original_height > 0:
                            print(f"[CameraWorker-{self.camera_id}] [OK] 视频文件原始尺寸: {self.source_original_width}x{self.source_original_height}")
                            print(f"[CameraWorker-{self.camera_id}]   解码尺寸: 640x640")
                            print(f"[CameraWorker-{self.camera_id}]   AI流还原: 将启用（{self.source_original_width}x{self.source_original_height}）")
                        else:
                            print(f"[CameraWorker-{self.camera_id}] [WARN] 无法获取视频文件原始分辨率，AI流将使用解码尺寸")
                    else:
                        print(f"[CameraWorker-{self.camera_id}] [WARN] 视频文件路径为空，无法获取原始分辨率")

                except Exception as e:
                    print(f"[CameraWorker-{self.camera_id}] [ERROR] 获取视频文件信息失败: {e}")
            else:
                # ========================================
                # 重要：检查ZLM推流状态（如果已推送则跳过启动）
                # ========================================
                # 根据项目约束：摄像机注册时已启动ZLM推流
                # 任务进程启动时，先检查推流状态，如果已存在则跳过启动
                print(f"[CameraWorker-{self.camera_id}] 检查ZLM推流状态...")

                stream_exists = False
                try:
                    from src.cameras.stream_service import StreamService, check_stream_online
                    from src.cameras.camera_manager import get_camera_manager

                    # 获取摄像机信息和流媒体服务器配置
                    camera_manager = get_camera_manager()
                    camera = camera_manager.get_camera(self.camera_id)

                    if camera:
                        # 获取流媒体服务器配置
                        stream_service = StreamService(db_path='data/tasks.db')
                        server = stream_service._get_streaming_server()

                        if server:
                            server_host = server.get('host', '127.0.0.1')
                            http_port = server.get('http_port', server.get('api_port', 88))
                            secret = server.get('secret') or server.get('config', {}).get('secret', '035c73f7-bb6b-4880-a316-36af2cf4c6fb')
                            app = 'AI'
                            stream_id = self.camera_id

                            # 检查流是否已经在线
                            stream_exists = check_stream_online(server_host, http_port, secret, app, stream_id)

                            if stream_exists:
                                print(f"[CameraWorker-{self.camera_id}] [OK] ZLM推流已存在（流已在线）")
                            else:
                                # 流不存在，启动推流
                                print(f"[CameraWorker-{self.camera_id}] [WARN] ZLM推流不存在，启动推流...")
                                result = stream_service.start_stream(self.camera_id)

                                if result.get('success'):
                                    print(f"[CameraWorker-{self.camera_id}] [OK] ZLM推流已启动")
                                    stream_exists = True
                                else:
                                    print(f"[CameraWorker-{self.camera_id}] [WARN] ZLM推流启动失败: {result.get('message', '未知错误')}")
                        else:
                            print(f"[CameraWorker-{self.camera_id}] [WARN] 没有配置流媒体服务器")
                    else:
                        print(f"[CameraWorker-{self.camera_id}] [WARN] 摄像机信息获取失败")

                    # ========================================
                    # 新增：从 ZLM 获取原始视频流尺寸（用于坐标映射）
                    # ========================================
                    if stream_exists and camera and server:
                        try:
                            from src.cameras.stream_service import get_stream_media_info

                            # 获取 ZLM 流的媒体信息
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

                                print(f"[CameraWorker-{self.camera_id}] [OK] ZLM 原始尺寸: {self.zlm_original_width}x{self.zlm_original_height}")
                                print(f"[CameraWorker-{self.camera_id}]   检测尺寸: 640x640")
                                print(f"[CameraWorker-{self.camera_id}]   坐标映射: 将启用")
                            else:
                                print(f"[CameraWorker-{self.camera_id}] [WARN] 无法获取 ZLM 流媒体信息，坐标映射禁用")

                        except Exception as e:
                            print(f"[CameraWorker-{self.camera_id}] [WARN] 获取 ZLM 原始尺寸失败: {e}")

                except Exception as stream_err:
                    print(f"[CameraWorker-{self.camera_id}] [WARN] ZLM推流检查异常: {stream_err}")
                    print(f"[CameraWorker-{self.camera_id}] 将继续尝试连接...")
            
            # ========================================
            # 初始化解码器（区分RTSP流和视频文件）
            # ========================================
            from src.decoders.video_decoder import VideoDecoder

            # 判断是视频文件还是RTSP流
            camera_protocol = self.camera_config.get('protocol', 'rtsp')
            is_video_file = camera_protocol == 'file'

            decoder_config = {
                'width': self.decoder_cfg.get('width', 640),
                'height': self.decoder_cfg.get('height', 640),
                'fps': self.decoder_cfg.get('fps', 25),  # ✅ 修复：添加FPS配置（默认25）
                'hwaccel_preset': self.decoder_cfg.get('hwaccel_preset', 'preset-dxva2'),  # 使用新版参数（项目约束）
                'rtsp_transport': self.decoder_cfg.get('rtsp_transport', 'tcp'),
                'reconnect': self.decoder_cfg.get('reconnect', True),
                'reconnect_delay': self.decoder_cfg.get('reconnect_delay', 5),
                'buffer_size': self.decoder_cfg.get('buffer_size', 1048576),
                'frame_queue_size': self.decoder_cfg.get('frame_queue_size', 30),

                # 新架构配置参数（默认启用）
                'use_ring_buffer': self.decoder_cfg.get('use_ring_buffer', True),
                'ring_buffer_block_count': self.decoder_cfg.get('ring_buffer_block_count', 5),
                'index_queue_size': self.decoder_cfg.get('index_queue_size', 30),

                # 摄像机ID和任务ID（避免共享内存冲突）
                'camera_id': self.camera_id,
                'task_id': str(self.task_id) if self.task_id else ''  # ✅ 转换为字符串
            }

            # ✅ 调试日志：检查task_id和camera_id
            print(f"[CameraWorker-{self.camera_id}] 创建decoder_config:")
            print(f"  camera_id={self.camera_id}, task_id={self.task_id} (类型: {type(self.task_id).__name__})")
            print(f"  共享内存名称前缀: sagesight_{str(self.task_id) if self.task_id else ''}_{self.camera_id}_block_*")

            # 根据类型设置输入源
            if is_video_file:
                decoder_config['video_file'] = self.rtsp_url  # 视频文件路径（不带file://前缀）
                decoder_config['rtsp_url'] = ''
                print(f"[CameraWorker-{self.camera_id}] 视频文件输入: {self.rtsp_url}")
            else:
                decoder_config['rtsp_url'] = self.rtsp_url  # RTSP流地址
                decoder_config['video_file'] = ''
                print(f"[CameraWorker-{self.camera_id}] RTSP流输入: {self.rtsp_url}")

            self.decoder = VideoDecoder(decoder_config)
            self.decoder.start()

            print(f"[CameraWorker-{self.camera_id}] 解码器初始化成功")
            print(f"[CameraWorker-{self.camera_id}]   分辨率: {decoder_config['width']}x{decoder_config['height']}")
            print(f"[CameraWorker-{self.camera_id}]   硬件加速: {decoder_config['hwaccel_preset']}")
            
            # 初始化跟踪器
            tracker_config = {
                'track_thresh': self.tracker_cfg.get('track_thresh', 0.30),
                'low_score_thresh': self.tracker_cfg.get('low_score_thresh', 0.10),
                'match_thresh': self.tracker_cfg.get('match_thresh', 0.50),
                'max_age': self.tracker_cfg.get('max_age', 45),
                'n_init': self.tracker_cfg.get('n_init', 2),
                'max_history_length': self.tracker_cfg.get('max_history_length', 30),
                'use_reid': self.tracker_cfg.get('use_reid', False)
            }
            
            self.tracker = self.tracker_class(**tracker_config)

            logger.info(f"[CameraWorker-{self.camera_id}] 跟踪器初始化成功")
            logger.info(f"[CameraWorker-{self.camera_id}]   track_thresh: {tracker_config['track_thresh']}")
            logger.info(f"[CameraWorker-{self.camera_id}]   max_age: {tracker_config['max_age']}")

            # 初始化预处理器（从配置读取参数）
            # ✅ 优化：不再创建预处理器实例（检测器进程已处理）
            # 预处理器在检测器进程中创建，CameraWorker无需重复创建
            # 节省内存：每个摄像机约10MB

            # ========================================
            # 全局检测池日志（已在MultiCameraExecutor注册）
            # ========================================
            print(f"[CameraWorker-{self.camera_id}] 使用全局检测进程池")
            print(f"[CameraWorker-{self.camera_id}]   共享内存名称: {self.decoder.shm_name}")
            print(f"[CameraWorker-{self.camera_id}]   任务ID: {self.task_id}")
            # 初始化运动检测器（可选）
            self._init_motion_detector()
            
            # 初始化事件输出器（可选）
            self._init_event_output()
            
            # 初始化 GIF 生成器（可选）
            self._init_gif_generator()
            
            # ========================================
            # 新增：初始化 Zone 区域管理器（借鉴 Frigate）
            # ========================================
            if self.zones_config:
                from src.detection.zone_manager import ZoneManager, ZoneConfig
                
                self.zone_manager = ZoneManager()
                
                # 从配置加载 Zone
                for zone_data in self.zones_config:
                    zone_config = ZoneConfig.from_dict(zone_data)
                    self.zone_manager.add_zone(zone_config)
                
                print(f"[CameraWorker-{self.camera_id}] Zone 区域管理器初始化成功")
                print(f"[CameraWorker-{self.camera_id}]   区域数量: {len(self.zone_manager.zones)}")
                print(f"[CameraWorker-{self.camera_id}]   必需区域: {len(self.zone_manager.required_zones)}")
            else:
                print(f"[CameraWorker-{self.camera_id}] Zone 区域管理器未配置")
            
            # ========================================
            # 新增：启动事件输出线程（避免阻塞检测）
            # ========================================
            logger.info(f"[CameraWorker-{self.camera_id}] 检查事件线程状态...")
            logger.info(f"[CameraWorker-{self.camera_id}]   hasattr(self, 'event_output_thread'): {hasattr(self, 'event_output_thread')}")

            if hasattr(self, 'event_output_thread'):
                logger.info(f"[CameraWorker-{self.camera_id}]   self.event_output_thread is None: {self.event_output_thread is None}")
                logger.info(f"[CameraWorker-{self.camera_id}]   type: {type(self.event_output_thread)}")

                if self.event_output_thread:
                    try:
                        self.event_output_thread.start()
                        logger.info(f"[CameraWorker-{self.camera_id}] [OK] 事件输出线程已启动")
                    except Exception as e:
                        logger.error(f"[CameraWorker-{self.camera_id}] [ERROR] 事件输出线程启动失败: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    logger.warning(f"[CameraWorker-{self.camera_id}] [WARN] event_output_thread 为 None，跳过启动")
            else:
                logger.warning(f"[CameraWorker-{self.camera_id}] [WARN] event_output_thread 不存在，跳过启动")

            # 强制刷新 stdout，确保日志立即输出
            import sys
            sys.stdout.flush()
            sys.stderr.flush()

            return True
            
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] 初始化失败: {e}")
            return False
    
    def _detect_with_pool(self, frame_index) -> List[Dict]:
        """
        使用全局检测进程池检测（异步模式）

        Args:
            frame_index: 帧索引（从解码器获取）

        Returns:
            list: 检测结果列表
        """
        if not self.detection_queue or not self.result_queue:
            logger.warning(f"[CameraWorker-{self.camera_id}] 全局检测进程池未初始化")
            return []

        try:
            from src.detectors.detection_queue import DetectionTask

            # ✅ 创建检测任务
            task = DetectionTask(
                camera_id=self.camera_id,
                frame_seq=frame_index.sequence_num,
                shm_block_id=frame_index.shm_block_id,
                shm_name=self.decoder.shm_name,
                timestamp_ns=frame_index.timestamp_ns,
                frame_shape=(self.decoder.height, self.decoder.width),
                output_event_name=f"detection_done_{self.camera_id}",
                task_id=str(self.task_id)  # 确保task_id是字符串
            )

            # 提交任务到全局检测进程池（非阻塞）
            try:
                self.detection_queue.put_nowait(task)

            except queue.Full:
                # ✅ 队列满是正常情况（检测器处理速度跟不上提交速度）
                # 降级为WARNING，避免刷屏
                logger.warning(f"[CameraWorker-{self.camera_id}] 检测队列已满，跳过帧 {frame_index.sequence_num}")
                return []

            except Exception as e:
                # ✅ 其他异常作为错误处理
                import traceback
                error_msg = str(e) if str(e) else f"{type(e).__name__}"
                logger.error(f"[CameraWorker-{self.camera_id}] [ERROR] 检测队列提交失败，跳过帧 {frame_index.sequence_num}: {error_msg}")
                traceback.print_exc()
                return []

            # ✅ 异步流水线逻辑：先尝试获取结果，如果没有则返回空（下一帧再获取）
            # 设计意图：
            # - 第N帧：提交任务，检查结果队列（可能为空）
            # - 第N+1帧：提交任务，检查结果队列（可能有第N帧的结果）
            # - 延迟1帧，但保持流水线高效
            try:
                # ✅ 修复：直接使用get_nowait()，捕获Empty异常作为正常情况
                # 注意：新架构下，每个摄像机有独立队列，无需检查camera_id
                result = self.result_queue.get_nowait()

                if result and 'detections' in result:
                    # ✅ camera_id匹配（因为队列独立，不会混淆）
                    result_frame_seq = result.get('frame_seq', 'unknown')
                    num_detections = len(result['detections'])

                    # 读取RGB帧（避免重复预处理）
                    if 'rgb_frame' in result:
                        rgb_frame = result['rgb_frame']
                        if rgb_frame is not None:
                            self._last_rgb_from_detector = rgb_frame
                        else:
                            logger.warning(f"[CameraWorker-{self.camera_id}] [WARN] RGB帧为空")
                            self._last_rgb_from_detector = None
                    else:
                        logger.debug(f"[CameraWorker-{self.camera_id}] 结果中无RGB帧（可能无检测结果）")
                        self._last_rgb_from_detector = None

                    return result['detections']
                else:
                    logger.warning(f"[CameraWorker-{self.camera_id}] [WARN] 结果格式错误: {result}")

            except queue.Empty:
                # ✅ 结果队列空是正常情况（异步流水线，结果可能还未返回）
                logger.debug(f"[CameraWorker-{self.camera_id}] 结果队列空，等待下一帧获取")
                pass

            except Exception as e:
                # ✅ 其他异常才作为错误处理
                import traceback
                logger.error(f"[CameraWorker-{self.camera_id}] [ERROR] 结果队列读取失败: {e}")
                logger.error(f"[CameraWorker-{self.camera_id}] 异常类型: {type(e).__name__}")
                traceback.print_exc()

            return []

        except Exception as e:
            logger.error(f"[CameraWorker-{self.camera_id}] 全局检测进程池检测失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _init_motion_detector(self):
        """初始化运动检测器 - 从任务表的 motion_detection_enabled 字段判断"""
        # 检查任务表的 motion_detection_enabled 字段，而不是 motion_config JSON
        if not self.task_config.get('motion_detection_enabled', False):
            print(f"[CameraWorker-{self.camera_id}] 运动检测已禁用（任务表 motion_detection_enabled=False）")
            return
        
        try:
            # ✅ 使用轻量级运动检测器（基于Frigate实现，比MOG2快5倍）
            from src.motion.lightweight_motion import LightweightMotionDetector

            # 从数据库配置构建运动检测参数（轻量级版本参数更少）
            motion_config = {
                'frame_shape': (self.decoder_cfg.get('height', 640), self.decoder_cfg.get('width', 640)),
                'motion_height': self.motion_cfg.get('motion_height', 180),  # 降采样分辨率
                'threshold': self.motion_cfg.get('threshold', 25),  # 运动阈值
                'delta_alpha': self.motion_cfg.get('delta_alpha', 0.2),  # 平均帧差更新速率
                'frame_alpha': self.motion_cfg.get('frame_alpha', 0.01),  # 背景更新速率（Frigate默认）
                'improve_contrast': self.motion_cfg.get('improve_contrast', False),  # 对比度增强（默认禁用以提升性能）
            }

            self.motion_detector = LightweightMotionDetector(**motion_config)

            logger.info(f"[CameraWorker-{self.camera_id}] 运动检测器初始化成功（轻量级，基于Frigate实现）")
            logger.info(f"[CameraWorker-{self.camera_id}]   motion_height: {motion_config['motion_height']}（降采样）")
            logger.info(f"[CameraWorker-{self.camera_id}]   threshold: {motion_config['threshold']}")
            logger.info(f"[CameraWorker-{self.camera_id}]   delta_alpha: {motion_config['delta_alpha']}（平滑噪声）")
            logger.info(f"[CameraWorker-{self.camera_id}]   frame_alpha: {motion_config['frame_alpha']}（背景更新）")

        except Exception as e:
            logger.error(f"[CameraWorker-{self.camera_id}] 运动检测器初始化失败: {e}")
            # 运动检测可选，失败不影响主流程

    def _format_detections_summary(self, detections: List[Dict]) -> str:
        """
        格式化检测结果摘要（用于日志）

        Args:
            detections: 检测结果列表

        Returns:
            str: 格式化的摘要字符串，如 "person:2(conf:0.40-0.50,avg:0.45), car:1(conf:0.60-0.60,avg:0.60)"
        """
        if not detections:
            return "无目标"

        from collections import defaultdict
        class_info = defaultdict(list)
        for det in detections:
            class_name = det.get('class_name', 'unknown')
            conf = det.get('confidence', 0.0)
            class_info[class_name].append(conf)

        # 格式化输出：类别:数量(置信度范围)
        parts = []
        for cls, confs in class_info.items():
            min_conf = min(confs)
            max_conf = max(confs)
            avg_conf = sum(confs) / len(confs)
            parts.append(f"{cls}:{len(confs)}(conf:{min_conf:.2f}-{max_conf:.2f},avg:{avg_conf:.2f})")

        return ', '.join(parts)

    def _log_with_interval(self, log_key: str, interval: float, message: str):
        """
        限制日志输出频率（避免刷屏）

        Args:
            log_key: 日志时间戳的属性名（如 '_motion_log_time'）
            interval: 输出间隔（秒）
            message: 日志消息
        """
        if not hasattr(self, log_key):
            setattr(self, log_key, time.time())

        if time.time() - getattr(self, log_key) >= interval:
            logger.info(message)
            setattr(self, log_key, time.time())

    def _init_event_output(self):
        """初始化事件输出器 - 从数据库配置读取"""
        if not self.event_cfg.get('enabled', False):
            logger.info(f"[CameraWorker-{self.camera_id}] 事件输出已禁用")
            return

        try:
            from src.events.event_output import EventOutput

            # 从数据库嵌套配置读取（正确方式）
            image_output_cfg = self.event_cfg.get('image_output', {})
            gif_cfg = self.event_cfg.get('gif', {})
            compression_cfg = self.event_cfg.get('compression', {})
            filter_cfg = self.event_cfg.get('filter', {})

            # ✅ 关键修复：从system_config.yaml读取storage、mqtt、websocket配置（系统级配置）
            import yaml
            from pathlib import Path
            system_config_path = Path(project_root) / 'config' / 'system_config.yaml'
            with open(system_config_path, 'r', encoding='utf-8') as f:
                system_config = yaml.safe_load(f)

            storage_cfg = system_config.get('storage', {})
            mqtt_cfg = system_config.get('mqtt', {})
            websocket_cfg = system_config.get('websocket', {})

            # 从数据库配置构建事件输出参数（完整配置）
            event_config = {
                'enabled': self.event_cfg.get('enabled', True),  # 从任务表的event_config读取
                'camera_id': self.camera_id,

                # ✅ 新增：传递完整的系统配置（用于生成 HTTP URL）
                'system_config': system_config,

                # ✅ 关键：传递storage配置（从system_config.yaml读取）
                'storage': storage_cfg,

                # ✅ MQTT配置（从system_config.yaml读取，系统级配置）
                'mqtt': mqtt_cfg,

                # ✅ WebSocket配置（从system_config.yaml读取，系统级配置）
                'websocket': websocket_cfg,

                # 图片输出配置（关键！正确读取嵌套的image_output配置）
                'image_output': {
                    'method': image_output_cfg.get('method', 'both'),  # ✅ 从image_output子字典读取
                    'minio_bucket': image_output_cfg.get('minio_bucket', 'sagesight-images'),
                    'ftp_remote_dir': image_output_cfg.get('ftp_remote_dir', '/sagesight/images'),
                    'local_backup': image_output_cfg.get('local_backup', True),  # ✅ 从image_output子字典读取
                    'output_dir': image_output_cfg.get('output_dir', 'output/images'),  # ✅ 从image_output子字典读取
                    'local_retention_days': image_output_cfg.get('local_retention_days', 7),
                    'auto_cleanup': image_output_cfg.get('auto_cleanup', True),
                    'cleanup_interval_hours': image_output_cfg.get('cleanup_interval_hours', 24)
                },

                # 压缩配置（从compression子字典读取）
                'compression': {
                    'enabled': compression_cfg.get('enabled', True),
                    'quality': compression_cfg.get('quality', 80),
                    'max_width': compression_cfg.get('max_width', 640),
                    'max_height': compression_cfg.get('max_height', 480)
                },

                # 过滤配置（target_classes从任务表的target_classes字段读取，与检测器保持一致）
                'filter': {
                    'target_classes': self.task_config.get('target_classes', []),
                    'min_confidence': filter_cfg.get('min_confidence', 0.5)
                }
            }

            self.event_output = EventOutput(event_config)
            self.event_output.task_id = self.task_id
            self.event_output.algorithm = self.algorithm

            # ========================================
            # 新增：创建事件输出线程（避免阻塞检测）
            # ========================================
            from src.events.event_output_thread import EventOutputThread
            
            max_queue_size = event_config.get('event_queue_size', 100)
            self.event_output_thread = EventOutputThread(
                event_output=self.event_output,
                max_queue_size=max_queue_size
            )

            logger.info(f"[CameraWorker-{self.camera_id}] 事件输出器初始化成功")
            logger.info(f"[CameraWorker-{self.camera_id}]   图片本地保存: {event_config['image_output']['local_backup']}")
            logger.info(f"[CameraWorker-{self.camera_id}]   图片保存目录: {event_config['image_output']['output_dir']}")
            logger.info(f"[CameraWorker-{self.camera_id}]   图片输出方法: {event_config['image_output']['method']}")
            logger.info(f"[CameraWorker-{self.camera_id}]   MinIO配置: {storage_cfg.get('minio', {}).get('enabled', False)}")
            logger.info(f"[CameraWorker-{self.camera_id}]   FTP配置: {storage_cfg.get('ftp', {}).get('enabled', False)}")
            logger.info(f"[CameraWorker-{self.camera_id}]   事件线程队列: {max_queue_size}")

        except Exception as e:
            logger.error(f"[CameraWorker-{self.camera_id}] 事件输出器初始化失败: {e}", exc_info=True)
            # 事件输出可选
    
    def _init_gif_generator(self):
        """初始化 GIF 生成器"""
        if not self.generate_gif:
            print(f"[CameraWorker-{self.camera_id}] GIF生成已禁用")
            return
        
        try:
            from src.events.gif_generator import GIFGenerator
            
            # ✅ 从system_config.yaml读取storage配置（与EventOutput一致）
            import yaml
            from pathlib import Path
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            system_config_path = Path(project_root) / 'config' / 'system_config.yaml'
            with open(system_config_path, 'r', encoding='utf-8') as f:
                system_config = yaml.safe_load(f)
            storage_cfg = system_config.get('storage', {})
            
            gif_cfg = self.event_cfg.get('gif', {})
            
            gif_config = {
                'max_frames': self.gif_max_frames,
                'fps': self.gif_fps,
                'max_width': self.gif_max_width,
                'max_height': self.gif_max_height,
                'output_dir': self.gif_output_dir,
                'draw_trajectory': self.gif_draw_trajectory,
                # 裁剪边距比例（与采集端一致，用于GIF中完整帧裁剪目标区域）
                'crop_margin_ratio': self.crop_margin_ratio,
                # GIF生成模式：'full' 全场景帧 | 'target' 裁剪目标区域
                'crop_mode': gif_cfg.get('crop_mode', 'full'),
                # 是否在GIF帧上绘制检测框（前端开关控制）
                'draw_bbox': self.gif_draw_bbox,
                # ✅ 输出方式：local | minio | ftp | both
                'output_method': gif_cfg.get('output_method', 'both'),
                # GIF存储配置
                'minio_bucket': gif_cfg.get('minio_bucket', 'sagesight-gifs'),
                'ftp_remote_dir': gif_cfg.get('ftp_remote_dir', '/sagesight/gifs'),
                # ✅ 传递全局storage配置
                'storage': storage_cfg
            }
            
            self.gif_generator = GIFGenerator(gif_config)
            print(f"[CameraWorker-{self.camera_id}] GIF生成器初始化成功")
            print(f"[CameraWorker-{self.camera_id}]   GIF MinIO配置: {storage_cfg.get('minio', {}).get('enabled', False)}")
            print(f"[CameraWorker-{self.camera_id}]   GIF FTP配置: {storage_cfg.get('ftp', {}).get('enabled', False)}")
            
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] GIF生成器初始化失败: {e}")
            # GIF生成可选，失败不影响主流程
    
    def run(self):
        """运行摄像机检测"""
        self.running = True

        # ✅ 初始化视频源类型判断（避免 UnboundLocalError）
        camera_protocol = self.camera_config.get('protocol', 'rtsp')
        is_video_file = camera_protocol == 'file'

        if not self.init_components():
            print(f"[CameraWorker-{self.camera_id}] 初始化失败，退出")
            import sys
            sys.stdout.flush()
            sys.stderr.flush()
            return

        print(f"[CameraWorker-{self.camera_id}] 开始检测")
        import sys
        sys.stdout.flush()
        sys.stderr.flush()

        # 启动 AI 流处理线程（如果启用）
        if self.ai_stream_enabled:
            self._start_ai_stream_thread()
            print(f"[CameraWorker-{self.camera_id}] AI 流处理线程已启动")

        # ========================================
        # 帧率限制配置（优先级）
        # ========================================
        # 1. display_config.target_fps（显示帧率限制）
        # 2. decoder_config.fps（解码帧率）
        # 3. 默认值10fps（避免CPU满载）
        fps_limit = self._display.get('target_fps', 0)
        if fps_limit <= 0:
            # 如果没有配置 target_fps，使用解码器的 fps 参数
            fps_limit = self.decoder_cfg.get('fps', 10)
            print(f"[CameraWorker-{self.camera_id}] 使用解码器帧率限制: {fps_limit}fps")
        else:
            print(f"[CameraWorker-{self.camera_id}] 使用显示帧率限制: {fps_limit}fps")

        # 运动检测模式标志（从任务配置读取,而不是从 motion_config 读取）
        motion_triggered_mode = self.task_config.get('motion_detection_enabled', False)
        # ✅ 新增：显示运动检测是否启用
        logger.info(f"[CameraWorker-{self.camera_id}] 运动检测模式: {motion_triggered_mode}")

        # 运动检测帧跳过间隔：每3帧检测一次运动，大幅减少CPU开销
        # 增加跳帧间隔：2 → 3，运动检测CPU消耗降低33%
        # 可通过数据库配置覆盖：motion_config.skip_interval
        motion_detect_skip_interval = self.motion_cfg.get('skip_interval', 3)
        motion_detect_skip_counter = 0
        # 缓存上一次运动检测结果（跳过帧时复用）
        last_has_motion = False
        if motion_triggered_mode:
            print(f"[CameraWorker-{self.camera_id}] 运动检测触发模式已启用")
            print(f"[CameraWorker-{self.camera_id}]   - 有运动目标时才启动检测")
            print(f"[CameraWorker-{self.camera_id}]   - 无运动目标时跳过检测（节约资源）")
            print(f"[CameraWorker-{self.camera_id}]   - 运动检测跳帧间隔: {motion_detect_skip_interval}")
        else:
            print(f"[CameraWorker-{self.camera_id}] 逐帧检测模式（运动检测已禁用）")
        
        # 统计计数器
        total_frames = 0
        motion_frames = 0
        detection_frames = 0
        skipped_frames = 0
        
        # ✅ 性能分析：用于跟踪每个步骤的耗时
        perf_stats = {
            'decode_ms': [],      # 解码耗时（毫秒）
            'preprocess_ms': [],  # 预处理耗时（毫秒）
            'motion_ms': [],      # 运动检测耗时（毫秒）
            'detect_ms': [],      # 检测耗时（毫秒）
            'track_ms': [],       # 跟踪耗时（毫秒）
            'event_ms': [],       # 事件处理耗时（毫秒）
            'total_ms': []        # 总耗时（毫秒）
        }
        perf_log_interval = 100  # 每100帧输出一次性能分析

        while self.running:
            try:
                # ========================================
                # 唯一架构：索引队列 + 环形缓冲区（零拷贝）
                # ========================================
                # 性能计时
                cycle_start = time.time()

                # 从索引队列获取帧索引（阻塞等待）
                get_index_start = time.time()
                frame_index = self.decoder.get_frame_index()
                get_index_time = time.time() - get_index_start

                if frame_index is None:
                    # 没有帧索引：适当休眠，避免CPU过度占用
                    # 建议休眠50-100毫秒，而不是10毫秒
                    time.sleep(0.05)
                    continue

                # 根据索引从环形缓冲区读取帧数据（零拷贝）
                read_frame_start = time.time()
                header, y_plane, u_plane, v_plane = self.decoder.ring_buffer.read_frame_i420(frame_index.shm_block_id)
                read_frame_time = time.time() - read_frame_start

                # ✅ 性能分析：解码耗时
                decode_time_ms = (get_index_time + read_frame_time) * 1000
                perf_stats['decode_ms'].append(decode_time_ms)
                
                self.frame_count += 1
                total_frames += 1
                
                # 首帧日志
                if total_frames == 1:
                    print(f"[CameraWorker-{self.camera_id}] 首帧接收成功（索引队列+环形缓冲区）")
                    print(f"[CameraWorker-{self.camera_id}]   块ID: {frame_index.shm_block_id}")
                    print(f"[CameraWorker-{self.camera_id}]   序列号: {frame_index.sequence_num}")
                    print(f"[CameraWorker-{self.camera_id}]   队列延迟: {frame_index.queue_delay_ms:.2f}ms")

                # ✅ 初始化RGB帧为None（按需生成）
                rgb_frame = None

                # ========================================
                # 运动检测（优化：预处理前，直接使用Y平面）
                # ========================================
                # ✅ 性能优化：运动检测直接使用Y平面（灰度图），无需预处理
                has_motion = True  # 默认有运动（未启用运动检测时）
                motion_detect_time = 0.0

                if motion_triggered_mode and self.motion_detector:
                    # 帧跳过机制：每 motion_detect_skip_interval 帧执行一次运动检测
                    motion_detect_skip_counter += 1
                    if motion_detect_skip_counter >= motion_detect_skip_interval:
                        motion_detect_skip_counter = 0
                        # ✅ 优化：直接使用Y平面进行运动检测（Y平面就是灰度图）
                        motion_detect_start = time.time()
                        has_motion = self.motion_detector.detect(y_plane)
                        motion_detect_time = time.time() - motion_detect_start
                        last_has_motion = has_motion

                        # ✅ 性能分析：运动检测耗时
                        motion_time_ms = motion_detect_time * 1000
                        perf_stats['motion_ms'].append(motion_time_ms)
                    else:
                        # 跳过帧：复用上一次运动检测结果
                        has_motion = last_has_motion
                        motion_detect_time = 0.0

                    # 如果没有运动，跳过YOLO推理（节约GPU资源），但仍发送AI视频流帧
                    if not has_motion:
                        skipped_frames += 1

                        # ✅ 改进：使用时间间隔限制（每5秒最多输出一次）
                        self._log_with_interval('_skip_log_time', 5.0, f"[CameraWorker-{self.camera_id}] 无运动目标，已跳过 {skipped_frames} 帧推理")

                        # 无运动目标：跳过推理，但仍然发送AI视频流帧（避免前端画面断续）
                        detections = []
                        detect_time = 0.0

                    else:
                        # 有运动目标，执行YOLO推理
                        motion_frames += 1

                        # ✅ 显示运动检测状态（限制频率）
                        self._log_with_interval('_motion_log_time', 2.0, f"[CameraWorker-{self.camera_id}] 检测到运动，开始YOLO推理")

                        # ========================================
                        # 使用全局检测进程池检测
                        # ========================================
                        detect_start = time.time()

                        detections = self._detect_with_pool(frame_index)

                        # ✅ 新增：根据任务表的 target_classes 过滤检测结果
                        target_classes = self.task_config.get('target_classes', [])
                        if target_classes:
                            detections = [det for det in detections if det.get('class_name') in target_classes]

                        detect_time = time.time() - detect_start

                        # ✅ 性能分析：检测耗时（包含预处理+推理，在检测进程中进行）
                        # 说明：全局检测进程池模式下，预处理在检测进程中完成
                        # detect_time 包含：共享内存读取 + 预处理 + 推理
                        detect_time_ms = detect_time * 1000
                        perf_stats['detect_ms'].append(detect_time_ms)

                        detection_frames += 1

                        # ✅ 显示检测到的目标（限制频率）
                        if detections:
                            detection_summary = self._format_detections_summary(detections)
                            self._log_with_interval('_detection_log_time', 2.0, f"[CameraWorker-{self.camera_id}] 检测到目标: {detection_summary}")
                
                # ========================================
                # 逐帧检测模式（直接推理）
                # ========================================
                else:
                    # ========================================
                    # 使用全局检测进程池检测
                    # ========================================
                    detect_start = time.time()

                    detections = self._detect_with_pool(frame_index)

                    # ✅ 类别过滤
                    target_classes = self.task_config.get('target_classes', [])
                    if target_classes:
                        detections = [det for det in detections if det.get('class_name') in target_classes]

                    # ✅ 性能分析：检测耗时
                    detect_time = time.time() - detect_start
                    detect_time_ms = detect_time * 1000
                    perf_stats['detect_ms'].append(detect_time_ms)

                    detection_frames += 1

                    # ✅ 显示检测到的目标（限制频率）
                    if detections:
                        detection_summary = self._format_detections_summary(detections)
                        self._log_with_interval('_detection_log_time', 2.0, f"[CameraWorker-{self.camera_id}] 检测到目标: {detection_summary}")
                
                # ========================================
                # 初始化 tracks 变量（避免未定义错误）
                # ========================================
                tracks = np.zeros((0, 7), dtype=np.float32)  # 默认为空数组

                # ✅ 性能分析：跟踪开始
                track_start = time.time()

                # 跟踪（仅当启用时）
                tracking_enabled = self.task_config.get('tracking_enabled', True)

                # ✅ 诊断日志：跟踪器状态
                logger.debug(f"[CameraWorker-{self.camera_id}] 跟踪器状态: tracking_enabled={tracking_enabled}, tracker存在={self.tracker is not None}")

                # 即使无检测结果也调用跟踪器：让跟踪器用运动模型预测目标位置，保持框连续
                if tracking_enabled and self.tracker:
                    # 转换为跟踪格式
                    if len(detections) > 0:
                        det_array = np.array([
                            [
                                det['bbox'][0], det['bbox'][1], det['bbox'][2], det['bbox'][3],
                                det['confidence'],
                                det['class_id'] if 'class_id' in det else 0
                            ]
                            for det in detections
                        ], dtype=np.float32)
                    else:
                        det_array = np.zeros((0, 6), dtype=np.float32)

                    tracks = self.tracker.update(det_array)

                    # ✅ 优化：使用时间间隔限制（每5秒最多输出一次）
                    if len(tracks) > 0:
                        self._log_with_interval('_track_log_time', 5.0, f"[CameraWorker-{self.camera_id}] 跟踪结果: {len(tracks)} 个目标, ID: {[int(t[4]) for t in tracks[:3]]}")

                    # ========================================
                    # 跟踪结果处理：三路输出
                    # ========================================
                    # 1. WebSocket实时推送（供其他前端画框）
                    # 2. 后端画框（供本项目前端播放）
                    # 3. 事件信息生成（轨迹、关键帧、GIF）

                    # ✅ 优化：优先使用检测进程返回的RGB帧（避免重复预处理）
                    if rgb_frame is None:
                        # 尝试从检测进程获取RGB帧
                        if hasattr(self, '_last_rgb_from_detector') and self._last_rgb_from_detector is not None:
                            rgb_frame = self._last_rgb_from_detector
                            self._last_rgb_from_detector = None  # 清空缓存（已使用）
                            logger.debug(f"[CameraWorker-{self.camera_id}] 使用检测进程返回的RGB帧（避免重复预处理）")
                        else:
                            # ✅ 检测器进程没有返回RGB帧：记录警告（架构问题）
                            logger.warning(f"[CameraWorker-{self.camera_id}] 检测器进程未返回RGB帧，无法绘制检测框")
                            # 注意：不再重新预处理，避免重复处理导致性能浪费

                    # 处理跟踪结果（仅当有跟踪目标时）
                    if len(tracks) > 0:
                        self._process_tracks(
                            tracks=tracks,
                            frame=rgb_frame,
                            frame_id=total_frames,
                            timestamp=datetime.now().isoformat()
                        )
                else:
                    # ✅ 诊断日志：为什么跳过跟踪
                    if not tracking_enabled:
                        logger.warning(f"[CameraWorker-{self.camera_id}] 跟踪已禁用（tracking_enabled=False）")
                    elif not self.tracker:
                        logger.error(f"[CameraWorker-{self.camera_id}] 跟踪器未初始化（self.tracker is None）")

                # ✅ 性能分析：跟踪耗时
                track_time = time.time() - track_start
                track_time_ms = track_time * 1000
                perf_stats['track_ms'].append(track_time_ms)
                    
                # ========================================
                # 根据视频源类型选择推送模式
                # ========================================
                # RTSP流（ZLM）：前端画框，只推送坐标元数据
                #   - 前端通过ZLM播放原始RTSP流
                #   - 后端通过WebSocket推送检测框坐标（detection_frame事件）
                #   - 前端接收到坐标后在播放画面上叠加绘制检测框
                #   - 优点：减少带宽占用，前端可以灵活控制绘制样式
                #
                # 视频文件：后端画框，推送MJPEG流
                #   - 后端绘制检测框到帧上
                #   - 编码为JPEG并通过WebSocket推送（ai_stream_frame事件）
                #   - 前端直接显示带检测框的MJPEG流
                #   - 优点：简化前端实现，确保绘制效果一致性
                # ========================================

                # ✅ 调试：输出推送模式判断（降级为DEBUG，避免刷屏）
                logger.debug(
                    f"[{self.camera_id}] 推送模式判断: is_video_file={is_video_file}, "
                    f"protocol={self.camera_config.get('protocol')}, "
                    f"detections_count={len(detections)}, tracks_count={len(tracks)}"
                )

                if is_video_file:
                    # ========================================
                    # 视频文件模式：后端画框，推送MJPEG流
                    # ========================================
                    if self.ai_stream_enabled:
                        self.ai_stream_frame_counter += 1
                        if self.ai_stream_frame_counter >= self.ai_stream_interval:
                            self.ai_stream_frame_counter = 0
                            self._send_ai_stream_frame(rgb_frame, detections, tracks if self.tracker else [])

                    # ✅ 不推送坐标元数据（前端不需要画框）
                else:
                    # ========================================
                    # RTSP流模式：前端画框，推送坐标元数据
                    # ========================================
                    # 通过 HTTP API 推送坐标元数据到主进程（由主进程通过 Flask-SocketIO 广播）
                    # 前端接收 'detection_frame' 事件，在播放画面上叠加绘制检测框

                    # ✅ 修改：使用真实PTS时间戳（从frame_index获取）
                    pts_timestamp_ns = frame_index.timestamp_ns  # PTS时间戳（纳秒）

                    self._send_detection_metadata(
                        frame_id=total_frames,
                        timestamp_ns=pts_timestamp_ns,  # ✅ 新增：传入PTS时间戳
                        detections=detections,
                        tracks=tracks
                    )

                    # ✅ 不推送AI流（前端通过ZLM播放原始流+坐标叠加）
                # 显示（可选）
                if self._display.get('enabled', False):
                    self._display_frame(rgb_frame, detections, tracks if self.tracker else [])
                
                # ========================================
                # 帧缓存维护（仅在GIF启用时执行）
                # ========================================
                # 优化：如果GIF功能关闭，跳过帧缓存以减少CPU占用（约36%）
                if self.generate_gif:
                    cache_now = time.time()
                    # 记录原始帧尺寸（用于bbox缩放到缓存尺寸）
                    if self.frame_orig_w == 0 and rgb_frame is not None:
                        self.frame_orig_h, self.frame_orig_w = rgb_frame.shape[:2]
                    # 按采样率缓存降采样帧
                    if rgb_frame is not None and cache_now - self.frame_cache_last_ts >= 1.0 / self.frame_cache_fps:
                        try:
                            small = cv2.resize(rgb_frame, (self.gif_max_width, self.gif_max_height))
                            self.frame_cache.append((cache_now, total_frames, small))
                            self.frame_cache_last_ts = cache_now
                        except Exception:
                            pass
                    # 淘汰过期帧
                    cache_cutoff = cache_now - self.frame_cache_retention
                    while self.frame_cache and self.frame_cache[0][0] < cache_cutoff:
                        self.frame_cache.popleft()
                
                # 处理待生成的GIF事件（到期则生成）
                self._process_pending_gifs()
                
                # FPS 控制
                if fps_limit > 0:
                    elapsed = time.time() - cycle_start
                    if elapsed < 1.0 / fps_limit:
                        time.sleep(1.0 / fps_limit - elapsed)
                
                # FPS 计算（每5秒打印一次，减少CPU占用）
                current_time = time.time()
                if current_time - self.last_fps_time >= 5.0:
                    # ✅ 实际FPS：过去5秒实际处理的帧数 / 5秒
                    actual_fps = self.frame_count / (current_time - self.last_fps_time)

                    # ✅ 计算各个环节的实际处理帧数
                    motion_detect_frames_in_5s = motion_frames  # 运动检测触发的帧数
                    skip_frames_in_5s = skipped_frames  # 跳过的帧数
                    detection_frames_in_5s = detection_frames  # 检测处理的帧数

                    self.frame_count = 0
                    self.last_fps_time = current_time
                    motion_frames = 0
                    skipped_frames = 0
                    detection_frames = 0

                    # ✅ 输出实际FPS统计
                    logger.info(f"[CameraWorker-{self.camera_id}] 实际FPS统计:")
                    logger.info(f"  实际处理FPS: {actual_fps:.1f} 帧/秒")
                    logger.info(f"  运动检测触发: {motion_detect_frames_in_5s} 帧 ({motion_detect_frames_in_5s/5:.1f} fps)")
                    logger.info(f"  跳过检测帧: {skip_frames_in_5s} 帧 ({skip_frames_in_5s/5:.1f} fps)")
                    logger.info(f"  实际检测帧: {detection_frames_in_5s} 帧 ({detection_frames_in_5s/5:.1f} fps)")

                    # ✅ 性能分析：输出详细的耗时统计（每5秒输出一次）
                    # 修改：检查 decode_ms 是否非空（而不是 total_ms）
                    if len(perf_stats['decode_ms']) > 0:
                        # 计算平均耗时
                        avg_decode = sum(perf_stats['decode_ms'][-perf_log_interval:]) / min(perf_log_interval, len(perf_stats['decode_ms']))

                        # 预处理耗时（可能为空，如果在运动检测模式下跳过了预处理）
                        # 说明：全局检测进程池模式下，预处理在检测进程中进行，此处为0
                        avg_preprocess = 0.0
                        if len(perf_stats['preprocess_ms']) > 0:
                            avg_preprocess = sum(perf_stats['preprocess_ms'][-perf_log_interval:]) / min(perf_log_interval, len(perf_stats['preprocess_ms']))

                        # 检测耗时（可能为空，如果在运动检测模式下跳过了推理）
                        avg_detect = 0.0
                        if len(perf_stats['detect_ms']) > 0:
                            avg_detect = sum(perf_stats['detect_ms'][-perf_log_interval:]) / min(perf_log_interval, len(perf_stats['detect_ms']))

                        # 跟踪耗时（可能为空，如果禁用了跟踪）
                        avg_track = 0.0
                        if len(perf_stats['track_ms']) > 0:
                            avg_track = sum(perf_stats['track_ms'][-perf_log_interval:]) / min(perf_log_interval, len(perf_stats['track_ms']))

                        # 运动检测耗时（可选）
                        avg_motion = 0.0
                        if len(perf_stats['motion_ms']) > 0:
                            avg_motion = sum(perf_stats['motion_ms'][-perf_log_interval:]) / min(perf_log_interval, len(perf_stats['motion_ms']))

                        # 总耗时
                        avg_total = avg_decode + avg_preprocess + avg_motion + avg_detect + avg_track

                        # 计算占比
                        decode_pct = (avg_decode / avg_total * 100) if avg_total > 0 else 0
                        preprocess_pct = (avg_preprocess / avg_total * 100) if avg_total > 0 else 0
                        motion_pct = (avg_motion / avg_total * 100) if avg_total > 0 else 0
                        detect_pct = (avg_detect / avg_total * 100) if avg_total > 0 else 0
                        track_pct = (avg_track / avg_total * 100) if avg_total > 0 else 0

                        logger.info(f"[CameraWorker-{self.camera_id}] 性能分析（平均耗时）:")
                        logger.info(f"  解码: {avg_decode:.1f}ms ({decode_pct:.1f}%)")
                        # 预处理在全局检测进程池中完成，显示为0
                        if avg_preprocess > 0:
                            logger.info(f"  预处理: {avg_preprocess:.1f}ms ({preprocess_pct:.1f}%)")
                        if avg_motion > 0:
                            logger.info(f"  运动检测: {avg_motion:.1f}ms ({motion_pct:.1f}%)")
                        if avg_detect > 0:
                            # 检测耗时包含：预处理+推理（在检测进程中进行）
                            logger.info(f"  检测(含预处理): {avg_detect:.1f}ms ({detect_pct:.1f}%)")
                        logger.info(f"  跟踪+事件: {avg_track:.1f}ms ({track_pct:.1f}%)")
                        # ✅ 修复：防止除零错误（当所有步骤耗时都为0时）
                        if avg_total > 0:
                            # ✅ 理论FPS上限：假设没有队列等待、没有事件阻塞
                            theoretical_fps = 1000 / avg_total
                            logger.info(f"  单帧平均耗时: {avg_total:.1f}ms (理论FPS上限: {theoretical_fps:.1f}, 实际FPS: {actual_fps:.1f})")
                        else:
                            logger.info(f"  单帧平均耗时: {avg_total:.1f}ms (无性能数据)")
                
            except Exception as e:
                import traceback
                print(f"[CameraWorker-{self.camera_id}] 检测错误: {e}")
                print(f"[CameraWorker-{self.camera_id}] 错误堆栈:")
                traceback.print_exc()
                time.sleep(0.1)
        
        # 清理
        self.cleanup()
        print(f"[CameraWorker-{self.camera_id}] 检测结束")
    
    def _display_frame(self, frame, detections: List[Dict], tracks: List):
        """显示帧（如果启用）"""
        try:
            display_frame = frame.copy()
            
            # 读取配置
            det_color = tuple(self._det_box_cfg.get('color', [0, 255, 0]))
            det_thickness = self._det_box_cfg.get('thickness', 2)
            det_font_scale = self._det_box_cfg.get('font_scale', 0.5)
            det_label_color = tuple(self._det_box_cfg.get('label_color', [0, 255, 0]))
            
            track_color = tuple(self._track_box_cfg.get('color', [255, 0, 0]))
            track_thickness = self._track_box_cfg.get('thickness', 2)
            track_font_scale = self._track_box_cfg.get('font_scale', 0.5)
            track_label_color = tuple(self._track_box_cfg.get('label_color', [255, 0, 0]))
            
            traj_color = tuple(self._trajectory_cfg.get('color', [0, 255, 255]))
            traj_thickness = self._trajectory_cfg.get('thickness', 1)
            traj_point_size = self._trajectory_cfg.get('point_size', 2)
            traj_point_color = tuple(self._trajectory_cfg.get('point_color', [0, 0, 255]))
            
            debug_color = tuple(self._debug_info_cfg.get('text_color', [255, 255, 255]))
            debug_font_scale = self._debug_info_cfg.get('font_scale', 0.6)
            
            # 绘制检测框
            if self._display.get('show_detection_boxes', True):
                for det in detections:
                    bbox = det['bbox']
                    class_name = det['class_name']
                    confidence = det['confidence']
                    
                    cv2.rectangle(display_frame, 
                                 (int(bbox[0]), int(bbox[1])), 
                                 (int(bbox[2]), int(bbox[3])), 
                                 det_color, det_thickness)
                    
                    label = f"{class_name}: {confidence:.2f}"
                    cv2.putText(display_frame, label, 
                               (int(bbox[0]), int(bbox[1]) - 10),
                               cv2.FONT_HERSHEY_SIMPLEX, det_font_scale, det_label_color, det_thickness)
            
            # 绘制跟踪框和轨迹
            if len(tracks) > 0:
                for track in tracks:
                    track_id = int(track[4])
                    bbox = track[:4]
                    
                    if self._display.get('show_track_boxes', True):
                        cv2.rectangle(display_frame,
                                     (int(bbox[0]), int(bbox[1])),
                                     (int(bbox[2]), int(bbox[3])),
                                     track_color, track_thickness)
                        
                        # 从活动跟踪记录中获取类别和置信度
                        track_info = self.active_tracks.get(track_id, {})
                        class_name = self._get_class_name(track_info.get('class_id', 0))
                        confidence = track_info.get('confidence', 0.0)
                        label = f"ID: {track_id} {class_name}: {confidence:.2f}"
                        cv2.putText(display_frame, label,
                                   (int(bbox[0]), int(bbox[3]) + 20),
                                   cv2.FONT_HERSHEY_SIMPLEX, track_font_scale, track_label_color, track_thickness)
                    
                    # 绘制轨迹线
                    if self._display.get('show_trajectories', True) and self.enable_trajectory and track_id in self.track_trajectories:
                        points = self.track_trajectories[track_id]['points']
                        hist_len = self._display.get('trajectory_history_length', 30)
                        points = points[-hist_len:]  # 只显示最近 N 帧
                        if len(points) >= 2:
                            for i in range(1, len(points)):
                                pt1 = (int(points[i-1]['x']), int(points[i-1]['y']))
                                pt2 = (int(points[i]['x']), int(points[i]['y']))
                                cv2.line(display_frame, pt1, pt2, traj_color, traj_thickness)
                            # 绘制轨迹点
                            for pt in points:
                                cv2.circle(display_frame,
                                          (int(pt['x']), int(pt['y'])),
                                          traj_point_size, traj_point_color, -1)
            
            # 绘制调试信息
            y_pos = 30
            if self._debug_info_cfg.get('show_fps', True):
                cv2.putText(display_frame, f"FPS: {self.fps:.1f}",
                           (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, debug_font_scale, debug_color, 1)
                y_pos += 25
            if self._debug_info_cfg.get('show_detection_count', True):
                cv2.putText(display_frame, f"Detections: {len(detections)}",
                           (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, debug_font_scale, debug_color, 1)
                y_pos += 25
            if self._debug_info_cfg.get('show_track_count', True):
                cv2.putText(display_frame, f"Tracks: {len(tracks)}",
                           (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, debug_font_scale, debug_color, 1)
            
            # 显示
            window_title = self._display.get('window_title', f'Detection - {self.camera_id}')
            cv2.imshow(window_title, display_frame)
            cv2.waitKey(1)
            
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] 显示错误: {e}")
    
    def _process_tracks(self, tracks: np.ndarray, frame: np.ndarray, 
                       frame_id: int, timestamp: str):
        """
        处理跟踪结果：三路输出
        
        Args:
            tracks: 跟踪结果数组 (N, 6) [x1, y1, x2, y2, track_id, class_id]
            frame: 当前帧图像
            frame_id: 帧ID
            timestamp: 时间戳
        """
        if not self.event_output:
            return

        # ✅ 优化：每帧采集计数器（避免循环内重置）
        current_frame_captures = 0
        max_tracks_per_frame = 3  # 每帧最多采集3个目标的关键帧

        # 遍历所有跟踪目标
        for track in tracks:
            track_id = int(track[4])
            bbox = track[:4]
            confidence = float(track[5])  # score 在索引 5
            class_id = int(track[6]) if len(track) > 6 else 0  # cls_id 在索引 6
            
            # ========================================
            # 新增：Zone 区域检测（借鉴 Frigate）
            # ========================================
            zone_events = {}
            if self.zone_manager:
                # 检查对象所在的区域（进入/离开）
                zone_events = self.zone_manager.check_object_zones(track_id, tuple(bbox))
                
                # 发送区域事件
                for zone_name, event_type in zone_events.items():
                    self._emit_zone_event(track_id, zone_name, event_type, bbox, confidence)
            
            # ========================================
            # 新增：对象状态管理（借鉴 Frigate）
            # ========================================
            # 更新对象状态（静止/移动）
            object_state = self.object_state_manager.update_object(track_id, tuple(bbox))
            
            # ========================================
            # 新增：必需区域过滤（借鉴 Frigate）
            # ========================================
            # 如果启用了 Zone，检查对象是否在必需区域内
            if self.zone_manager and not self.zone_manager.should_emit_event(track_id, tuple(bbox)):
                # 对象不在必需区域内，跳过事件发送
                continue
            
            # 检查置信度是否达到事件触发阈值
            event_threshold = self.event_cfg.get('event_threshold', 0.7)
            should_trigger_event = confidence >= event_threshold
            
            # ========================================
            # 1. 轨迹数据收集
            # ========================================
            if self.enable_trajectory:
                self._update_trajectory(
                    track_id=track_id,
                    bbox=bbox,
                    class_id=class_id,
                    confidence=confidence,
                    frame_id=frame_id,
                    timestamp=timestamp
                )
            
            # ========================================
            # 2. 关键帧采集（首帧、中间帧）- 始终采集（用于GIF生成）
            # ========================================
            # 检查是否为新跟踪目标（首次出现）
            is_new_track = track_id not in self.active_tracks
            
            if is_new_track:
                # 首次出现：记录活动跟踪目标
                self.active_tracks[track_id] = {
                    'first_frame': frame_id,
                    'last_bbox': bbox,
                    'last_update': time.time(),
                    'start_time': time.time(),
                    'class_id': class_id,
                    'confidence': confidence,
                    # ❌ 不保存完整帧（避免内存泄漏）
                    # 'last_visible_frame': frame.copy(),
                }
                
                # 采集首帧（完整帧或裁剪帧）- 始终采集，用于GIF生成
                if self.save_full_on_start:
                    self._capture_frame(
                        track_id=track_id,
                        frame=frame,
                        bbox=bbox,
                        frame_id=frame_id,
                        is_full_frame=True,
                        capture_reason='track_start'
                    )
                
                print(f"[CameraWorker-{self.camera_id}] 新跟踪目标出现: ID={track_id}, 类别={class_id}, 置信度={confidence:.2f}")
            
            else:
                # 更新活动跟踪目标
                self.active_tracks[track_id]['last_bbox'] = bbox
                self.active_tracks[track_id]['last_update'] = time.time()
                # ❌ 不保存完整帧（避免内存泄漏）
                # self.active_tracks[track_id]['last_visible_frame'] = frame.copy()
                
                # 周期性采集（根据策略）- 优化：添加置信度过滤和数量限制
                if self.capture_strategy == 'periodic':
                    # ✅ 优化1：置信度过滤 - 只对高置信度目标采集关键帧
                    # 低置信度目标可能是误检，采集关键帧浪费CPU和内存
                    min_confidence_for_capture = 0.5  # 最小置信度阈值
                    if confidence < min_confidence_for_capture:
                        logger.debug(f"[CameraWorker-{self.camera_id}] 跳过低置信度目标采集: ID={track_id}, conf={confidence:.2f}")
                        continue  # 跳过低置信度目标

                    interval = self.capture_interval
                    last_capture_frame = self.track_frame_sequences.get(track_id, [{}])[-1].get('frame_id', 0)

                    if frame_id - last_capture_frame >= interval:
                        # ✅ 优化2：检查当前帧已采集数量
                        if current_frame_captures >= max_tracks_per_frame:
                            logger.debug(f"[CameraWorker-{self.camera_id}] 达到每帧采集上限，跳过: ID={track_id}")
                            continue

                        self._capture_frame(
                            track_id=track_id,
                            frame=frame,
                            bbox=bbox,
                            frame_id=frame_id,
                            is_full_frame=False,
                            capture_reason='periodic'
                        )
                        current_frame_captures += 1
        
        # ========================================
        # 3. 检查跟踪目标消失（生成GIF） - 在循环外部执行
        # ========================================
        # 获取当前帧的所有track_id（避免numpy数组布尔判断问题）
        current_track_ids = set()
        if len(tracks) > 0:
            for t in tracks:
                current_track_ids.add(int(t[4]))
        
        # 检查哪些活动跟踪目标已经消失
        disappeared_tracks = []
        for active_id, active_info in list(self.active_tracks.items()):
            # 如果这个活动ID不在当前跟踪结果中，且超过max_age未更新
            last_update = active_info['last_update']
            max_age_seconds = self.tracker_cfg.get('max_age', 30) * 0.04  # 假设25fps
            
            if active_id not in current_track_ids:
                if time.time() - last_update > max_age_seconds:
                    disappeared_tracks.append(active_id)
        
        # 处理消失的跟踪目标
        for disappeared_id in disappeared_tracks:
            # 采集消失时的完整帧（如果配置）
            if self.save_full_on_end:
                active_info = self.active_tracks[disappeared_id]
                last_bbox = active_info['last_bbox']
                
                # 使用目标最后可见时的帧（而非当前帧），确保框在目标上
                # 如果 last_visible_frame 不存在，则使用当前帧
                last_visible_frame = active_info.get('last_visible_frame', frame)
                
                self._capture_frame(
                    track_id=disappeared_id,
                    frame=last_visible_frame,  # 使用目标最后可见时的帧
                    bbox=last_bbox,
                    frame_id=frame_id,
                    is_full_frame=True,
                    capture_reason='track_end'
                )
            
            # 推入待生成GIF列表（延迟 post_seconds 后生成，以捕获"结束后5秒"的帧）
            if (self.generate_gif and self.gif_generator) or self.event_output:
                active_info = self.active_tracks[disappeared_id]
                end_time = time.time()
                pending = {
                    'track_id': disappeared_id,
                    'start_time': active_info.get('start_time', end_time),
                    'end_time': end_time,
                    'generate_time': end_time + self.gif_post_seconds,
                    'trajectory': dict(self.track_trajectories.get(disappeared_id, {})),
                    'frame_seq': list(self.track_frame_sequences.get(disappeared_id, [])),
                }
                self.pending_gifs.append(pending)
            
            # 清理 active_tracks（数据已复制到pending，可立即删除）
            del self.active_tracks[disappeared_id]
            
            print(f"[CameraWorker-{self.camera_id}] 跟踪目标消失: ID={disappeared_id}，等待{self.gif_post_seconds}秒后生成GIF")
    
    def _update_trajectory(self, track_id: int, bbox: np.ndarray, 
                          class_id: int, confidence: float, frame_id: int, timestamp: str):
        """
        更新轨迹数据
        
        Args:
            track_id: 跟踪ID
            bbox: 边界框
            class_id: 类别ID
            confidence: 置信度
            frame_id: 帧ID
            timestamp: 时间戳
        """
        # 初始化轨迹数据
        if track_id not in self.track_trajectories:
            self.track_trajectories[track_id] = {
                'points': [],
                'start_time': timestamp,
                'class_id': class_id,
                'max_confidence': 0.0
            }
        
        # 计算中心点
        center_x = (bbox[0] + bbox[2]) / 2
        center_y = (bbox[1] + bbox[3]) / 2
        
        # 添加轨迹点
        trajectory_point = {
            'x': center_x,
            'y': center_y,
            'bbox': bbox.tolist(),
            'time_sec': time.time(),
            'frame_id': frame_id,
            'timestamp': timestamp
        }
        
        trajectory = self.track_trajectories[track_id]
        trajectory['points'].append(trajectory_point)
        
        # 更新最大置信度
        if confidence > trajectory.get('max_confidence', 0.0):
            trajectory['max_confidence'] = confidence
        
        # 限制轨迹点数量
        if len(trajectory['points']) > self.trajectory_max_points:
            trajectory['points'] = trajectory['points'][-self.trajectory_max_points:]
        # 实时发送轨迹更新（已废弃，使用 Flask-SocketIO 推送）
        # 注：轨迹数据现在通过 _send_detection_metadata() 推送
    def _capture_frame(self, track_id: int, frame: np.ndarray, 
                      bbox: np.ndarray, frame_id: int, 
                      is_full_frame: bool, capture_reason: str):
        """
        采集关键帧
        
        Args:
            track_id: 跟踪ID
            frame: 帟图像
            bbox: 边界框
            frame_id: 帧ID
            is_full_frame: 是否为完整帧
            capture_reason: 采集原因（track_start, periodic, track_end）
        """
        # 初始化帧序列
        if track_id not in self.track_frame_sequences:
            self.track_frame_sequences[track_id] = []
        
        # 限制帧序列长度：超限时均匀降采样（保留全时间跨度的帧 + 始终保留最后一帧）
        if len(self.track_frame_sequences[track_id]) >= self.gif_max_frames:
            # 保留偶数索引帧（隔帧保留），腾出空间继续采集新帧
            seq = self.track_frame_sequences[track_id]
            sampled = seq[::2]
            # 始终保留最后一帧（track_end完整帧），避免降采样丢掉
            if len(seq) > 1 and sampled[-1] is not seq[-1]:
                sampled.append(seq[-1])
            self.track_frame_sequences[track_id] = sampled
        
        # 采集帧数据
        if is_full_frame:
            cropped_image = frame.copy()
            crop_offset = (0, 0)  # 完整帧无偏移
        else:
            cropped_image, crop_offset = self._crop_frame(frame, bbox)
        
        frame_data = {
            'image': cropped_image,
            'bbox': bbox.tolist(),
            'crop_offset': crop_offset,  # 裁剪偏移（用于精确调整bbox坐标）
            'frame_id': frame_id,
            'timestamp': datetime.now().isoformat(),
            'is_full_frame': is_full_frame,
            'capture_reason': capture_reason
        }
        
        self.track_frame_sequences[track_id].append(frame_data)
        
        print(f"[CameraWorker-{self.camera_id}] 关键帧采集: ID={track_id}, 原因={capture_reason}, 帧类型={is_full_frame}, 序列长度={len(self.track_frame_sequences[track_id])}")
    
    def _crop_frame(self, frame: np.ndarray, bbox: np.ndarray) -> tuple:
        """
        裁剪帧（保留目标区域）
        
        Args:
            frame: 完整帧
            bbox: 边界框
        
        Returns:
            (裁剪后的帧, (x1, y1))：裁剪图像 + 裁剪偏移（用于后续bbox坐标调整）
        """
        h, w = frame.shape[:2]
        
        # 扩展边界框（保留上下文）：按配置比例扩展，值越大裁剪范围越大
        margin_x = int((bbox[2] - bbox[0]) * self.crop_margin_ratio)
        margin_y = int((bbox[3] - bbox[1]) * self.crop_margin_ratio)
        
        x1 = max(0, int(bbox[0] - margin_x))
        y1 = max(0, int(bbox[1] - margin_y))
        x2 = min(w, int(bbox[2] + margin_x))
        y2 = min(h, int(bbox[3] + margin_y))
        
        return frame[y1:y2, x1:x2], (x1, y1)
    
    def _generate_track_gif(self, track_id: int):
        """
        生成跟踪过程的GIF动画
        
        Args:
            track_id: 跟踪ID
        """
        frame_sequence = self.track_frame_sequences.get(track_id, [])
        
        if not frame_sequence:
            print(f"[CameraWorker-{self.camera_id}] 无帧序列，跳过GIF生成: ID={track_id}")
            return
        
        try:
            # 生成GIF
            gif_filename = f"track_{track_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gif"
            gif_urls = self.gif_generator.generate(
                track_id=track_id,
                frames=frame_sequence,
                output_filename=gif_filename
            )
            
            if gif_urls:
                print(f"[CameraWorker-{self.camera_id}] GIF已生成: ID={track_id}, URLs={gif_urls}")
                
                # 保存GIF URLs到轨迹数据
                if track_id in self.track_trajectories:
                    self.track_trajectories[track_id]['gif_urls'] = gif_urls
            else:
                print(f"[CameraWorker-{self.camera_id}] GIF生成失败: ID={track_id}")
            
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] GIF生成失败: ID={track_id}, 错误={e}")
    
    def _process_pending_gifs(self):
        """处理到期的待生成GIF事件（在主循环中调用，避免线程并发问题）"""
        if not self.pending_gifs:
            return
        now = time.time()
        ready = [p for p in self.pending_gifs if now >= p['generate_time']]
        self.pending_gifs = [p for p in self.pending_gifs if now < p['generate_time']]
        for pending in ready:
            try:
                self._finalize_pending_gif(pending)
            except Exception as e:
                print(f"[CameraWorker-{self.camera_id}] 待处理GIF生成失败: ID={pending.get('track_id')}, 错误={e}")
    
    def _finalize_pending_gif(self, pending: dict):
        """
        完成待处理的GIF事件：从帧缓存提取[start-pre, end+post]时间窗的原始帧，
        生成GIF并发送事件。所有共享状态访问在主循环线程完成，
        仅GIF编码+上传+事件发送在后台线程执行。
        """
        tid = pending['track_id']
        
        # 防止同一目标重复产生事件
        if tid in self.processed_track_ids:
            print(f"[CameraWorker-{self.camera_id}] 目标已处理过事件，跳过: ID={tid}")
            return
        self.processed_track_ids.add(tid)
        # 定期清理已处理的track_ids，避免内存累积（保留最近100个）
        if len(self.processed_track_ids) > 200:
            print(f"[CameraWorker-{self.camera_id}] 清理processed_track_ids（当前{len(self.processed_track_ids)}个）")
            # 只保留最近100个，而不是全部清空
            self.processed_track_ids = set(list(self.processed_track_ids)[-100:])
            self.processed_track_ids.add(tid)
        
        traj = pending['trajectory']
        class_id = traj.get('class_id', 0)
        class_name = self._get_class_name(class_id)
        max_conf = traj.get('max_confidence', 0.0)
        
        # 事件过滤：未通过则跳过GIF生成和事件发送（避免无效I/O）
        if self.event_output and not self.event_output.should_send(class_name, max_conf):
            print(f"[CameraWorker-{self.camera_id}] 事件未通过过滤，跳过GIF和事件: ID={tid}, class={class_name}, conf={max_conf}")
            return
        
        # 从帧缓存提取 [start-pre, end+post] 时间窗的原始完整帧
        start_t = pending['start_time'] - self.gif_pre_seconds
        end_t = pending['end_time'] + self.gif_post_seconds
        cache_frames = [(ts, fid, img) for (ts, fid, img) in self.frame_cache if start_t <= ts <= end_t]
        
        # 构建GIF帧列表（全场景帧 + 事件期间绘制bbox/轨迹）
        gif_frames = self._build_gif_frames_from_cache(
            cache_frames, traj, pending['start_time'], pending['end_time']
        )
        
        frame_seq = pending['frame_seq']
        
        # 在后台线程执行GIF编码+上传+事件发送（耗时IO），不阻塞主循环
        import threading as _threading
        def _async_finalize(tid=tid, gif_frames=gif_frames, frame_seq=frame_seq,
                            traj=traj, class_name=class_name, max_conf=max_conf):
            try:
                # 生成GIF
                gif_urls = {}
                if self.generate_gif and self.gif_generator and gif_frames:
                    gif_filename = f"track_{tid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gif"
                    gif_urls = self.gif_generator.generate(
                        track_id=tid,
                        frames=gif_frames,
                        output_filename=gif_filename
                    )
                    if gif_urls:
                        print(f"[CameraWorker-{self.camera_id}] GIF已生成(帧缓存): ID={tid}, URLs={gif_urls}, 帧数={len(gif_frames)}")
                        traj['gif_urls'] = gif_urls
                    else:
                        print(f"[CameraWorker-{self.camera_id}] GIF生成失败: ID={tid}")
                
                # 临时放回数据供 _emit_track_event 读取
                self.track_frame_sequences[tid] = frame_seq
                self.track_trajectories[tid] = traj
                
                # 发送事件
                if self.event_output:
                    self._emit_track_event(
                        track_id=tid,
                        event_type='track_end',
                        frame_sequence=frame_seq
                    )
            except Exception as e:
                print(f"[CameraWorker-{self.camera_id}] GIF/事件后台处理失败: ID={tid}, 错误={e}")
            finally:
                self.track_frame_sequences.pop(tid, None)
                self.track_trajectories.pop(tid, None)
        
        # ========================================
        # 使用线程池执行GIF生成（避免线程泄漏）
        # ========================================
        # 修改说明：
        # - 原来每次GIF生成创建新线程，可能导致线程泄漏
        # - 现在使用ThreadPoolExecutor限制并发数（max_workers=2）
        # - 优点：
        #   1. 防止线程泄漏：线程池管理线程生命周期
        #   2. 限制并发：最多同时生成2个GIF，避免CPU占用过高
        #   3. 线程复用：提高性能，减少线程创建开销
        try:
            self.gif_executor.submit(_async_finalize)
        except Exception as e:
            # 线程池提交失败（例如线程池已关闭），回退到直接创建线程
            print(f"[CameraWorker-{self.camera_id}] 线程池提交失败，回退到直接创建线程: {e}")
            _threading.Thread(target=_async_finalize, daemon=True).start()
    
    def _build_gif_frames_from_cache(self, cache_frames: list, trajectory: dict,
                                      start_time: float, end_time: float) -> List[Dict]:
        """
        从帧缓存构建GIF帧列表（全场景帧，事件期间绘制bbox）。
        
        Args:
            cache_frames: [(time_sec, frame_id, frame_small)]
            trajectory: 轨迹数据 {'points': [{'bbox', 'time_sec', ...}], ...}
            start_time: 事件起始时间（time.time()）
            end_time: 事件结束时间（time.time()）
        
        Returns:
            GIF帧数据列表，每项含 image/bbox/crop_offset/is_full_frame 等
        """
        if not cache_frames:
            return []
        
        # 原始帧 -> 缓存帧的缩放比例
        if self.frame_orig_w > 0 and self.frame_orig_h > 0:
            scale_x = self.gif_max_width / self.frame_orig_w
            scale_y = self.gif_max_height / self.frame_orig_h
        else:
            scale_x = scale_y = 1.0
        
        # 提取轨迹点（带bbox+time_sec），按时间排序
        traj_points = trajectory.get('points', [])
        traj_pts_sorted = sorted(
            [p for p in traj_points if 'time_sec' in p and 'bbox' in p],
            key=lambda p: p['time_sec']
        )
        
        result = []
        for (ts, fid, img) in cache_frames:
            bbox_scaled = [0, 0, 0, 0]  # 默认无bbox（事件前/后的场景帧）
            
            # 仅在事件期间 [start, end] 绘制bbox
            if start_time <= ts <= end_time and traj_pts_sorted:
                # 找到时间最近的轨迹点
                nearest = min(traj_pts_sorted, key=lambda p: abs(p['time_sec'] - ts))
                bbox = nearest['bbox']
                # 缩放到缓存帧尺寸
                bbox_scaled = [
                    int(bbox[0] * scale_x),
                    int(bbox[1] * scale_y),
                    int(bbox[2] * scale_x),
                    int(bbox[3] * scale_y)
                ]
            
            frame_data = {
                'image': img,
                'bbox': bbox_scaled,
                'crop_offset': (0, 0),
                'frame_id': fid,
                'timestamp': datetime.fromtimestamp(ts).isoformat(),
                'is_full_frame': True,  # 全场景帧（gif_generator full模式不裁剪）
            }
            result.append(frame_data)
        
        return result
    
    def _emit_track_event(self, track_id: int, event_type: str,
                         frame_sequence: List[Dict]):
        """
        发送跟踪事件到MQTT

        Args:
            track_id: 跟踪ID
            event_type: 事件类型（track_start, track_end, track_update）
            frame_sequence: 帧序列
        """
        if not self.event_output:
            return

        # 获取轨迹数据
        trajectory = self.track_trajectories.get(track_id, {})

        # 获取GIF URLs（如果有）
        gif_urls = trajectory.get('gif_urls', {})
        
        # 获取最大置信度（从轨迹中所有检测中取最大值）
        max_confidence = trajectory.get('max_confidence', 0.0)
        if max_confidence == 0.0 and frame_sequence:
            # 回退：从帧序列中获取最大置信度
            for frame_data in frame_sequence:
                conf = frame_data.get('confidence', 0.0)
                if conf > max_confidence:
                    max_confidence = conf

        # 获取最后一帧的 bbox
        last_bbox = []
        if frame_sequence:
            last_bbox = frame_sequence[-1].get('bbox', [])
        
        # 构建事件数据
        event_data = {
            'track_id': track_id,
            'class_id': trajectory.get('class_id', 0),
            'class_name': self._get_class_name(trajectory.get('class_id', 0)),
            'confidence': max_confidence,  # ✅ 添加置信度字段（用于过滤）
            'bbox': last_bbox,  # ✅ 添加 bbox 字段
            'trajectory_points': trajectory.get('points', []),
            'frame_count': len(frame_sequence),
            'gif_urls': gif_urls,  # 改为 gif_urls 字典
            'start_time': trajectory.get('start_time', ''),
            'end_time': datetime.now().isoformat()
        }
        
        # ✅ 调试日志
        print(f"[CameraWorker-{self.camera_id}] 事件数据: class_id={trajectory.get('class_id', 0)}, class_name={event_data['class_name']}, bbox={last_bbox}")

        # 获取最后一帧的图片（用于保存）
        last_frame_image = None
        if frame_sequence:
            last_frame_data = frame_sequence[-1]
            last_frame_image = last_frame_data.get('image')
            is_full_frame = last_frame_data.get('is_full_frame', True)
            last_bbox = last_frame_data.get('bbox', [0, 0, 0, 0])

            # 在图片上绘制跟踪框和类别信息（受前端开关控制）
            if last_frame_image is not None and len(frame_sequence) > 0 and self.event_image_draw_bbox:
                # 确保bbox有有效的坐标
                if len(last_bbox) != 4:
                    print(f"[CameraWorker-{self.camera_id}] 警告: bbox坐标无效，跳过绘制: {last_bbox}")
                else:
                    if is_full_frame:
                        # 完整帧：bbox 坐标就是原图坐标，直接画
                        draw_bbox = last_bbox
                    else:
                        # 裁剪帧：用采集时存储的精确裁剪偏移调整 bbox
                        crop_x1, crop_y1 = last_frame_data.get('crop_offset', (0, 0))
                        draw_bbox = [
                            last_bbox[0] - crop_x1,
                            last_bbox[1] - crop_y1,
                            last_bbox[2] - crop_x1,
                            last_bbox[3] - crop_y1
                        ]
                        # 确保坐标不越界
                        img_h, img_w = last_frame_image.shape[:2]
                        draw_bbox[0] = max(0, min(draw_bbox[0], img_w))
                        draw_bbox[1] = max(0, min(draw_bbox[1], img_h))
                        draw_bbox[2] = max(0, min(draw_bbox[2], img_w))
                        draw_bbox[3] = max(0, min(draw_bbox[3], img_h))
                    # ✅ 修复：在tracks_array中添加confidence字段（索引7）
                    # 轨迹格式：[x1, y1, x2, y2, track_id, class_id, confidence]
                    max_conf = trajectory.get('max_confidence', 0.0)
                    tracks_array = np.array([[draw_bbox[0], draw_bbox[1], draw_bbox[2], draw_bbox[3],
                                             track_id, trajectory.get('class_id', 0), max_conf]], dtype=np.float32)
                    last_frame_image = self._draw_boxes_on_frame(
                        frame=last_frame_image,
                        detections=[],  # 事件图片只画跟踪框
                        tracks=tracks_array
                    )

        # 发送事件（异步提交到事件线程，避免阻塞）
        self.event_output_thread.submit_event(
            event_type=event_type,
            data=event_data,
            image=last_frame_image,  # ✅ 传入最后一帧的图片
            frame_id=frame_sequence[-1].get('frame_id', 0) if frame_sequence else 0,
            task_id=self.task_id,
            algorithm=self.algorithm
        )
    
    def _get_class_name(self, class_id: int) -> str:
        """
        根据类别ID获取类别名称
        
        Args:
            class_id: 类别ID
        
        Returns:
            类别名称
        """
        # 从 task_config 获取类别名称（全局检测池模式）
        class_names = self.task_config.get('class_names', [])
        if class_names and class_id < len(class_names):
            return class_names[class_id]
        return f'class_{class_id}'
    
    def _send_detection_metadata(self, frame_id: int, timestamp_ns: int, detections: List[Dict], tracks: np.ndarray):
        """
        发送检测框坐标元数据到前端（用于前端画框）

        通过 HTTP API 推送到主进程，由主进程通过 Flask-SocketIO 广播给前端。

        Args:
            frame_id: 帧ID
            timestamp_ns: PTS时间戳（纳秒）- ✅ 新增参数
            detections: 检测结果列表
            tracks: 跟踪结果数组
        """
        import requests

        # 构建检测框数据（✅ 优化：直接使用640×640坐标，不映射）
        detection_boxes = []
        for det in detections:
            detection_boxes.append({
                'bbox': det['bbox'],  # 直接使用640×640坐标
                'class_name': det['class_name'],
                'confidence': det['confidence'],
                'class_id': det['class_id']
            })

        # 构建跟踪框数据（✅ 优化：直接使用640×640坐标，不映射）
        track_boxes = []
        if len(tracks) > 0:
            for track in tracks:
                track_boxes.append({
                    'bbox': track[:4].tolist(),  # 直接使用640×640坐标
                    'track_id': int(track[4]),
                    'confidence': float(track[5]),  # score 在索引 5
                    'class_id': int(track[6]) if len(track) > 6 else 0  # cls_id 在索引 6
                })

        # 构建轨迹数据（✅ 优化：直接使用640×640坐标，不映射）
        trajectory_data = {}
        for track_id, trajectory_info in list(self.track_trajectories.items()):
            mapped_points = []
            for point in trajectory_info['points']:
                mapped_point = point.copy()
                # 直接使用640×640坐标
                mapped_points.append(mapped_point)
            trajectory_data[track_id] = mapped_points

        # 通过 HTTP API 推送到主进程
        try:
            # ✅ 修改：使用传入的PTS时间戳（纳秒），转换为毫秒
            timestamp_ms = int(timestamp_ns / 1_000_000)  # 纳秒转毫秒

            payload = {
                'task_id': self.task_id,
                'camera_id': self.camera_id,
                'frame_id': frame_id,
                'frame_seq': frame_id,  # 帧序号（与frame_id一致）
                'timestamp': timestamp_ms,  # ✅ 修改：使用PTS时间戳（毫秒）
                'detections': detection_boxes,
                'tracks': track_boxes,
                'trajectories': trajectory_data
            }

            # ✅ 调试：输出推送数据
            logger.debug(
                f"[{self.camera_id}] 推送检测数据到HTTP API: "
                f"frame_id={frame_id}, timestamp={timestamp_ms}, "
                f"detections={len(detection_boxes)}, tracks={len(track_boxes)}"
            )

            response = requests.post(
                'http://127.0.0.1:5555/api/detection-metadata/push',
                json=payload,
                timeout=1.0  # ✅ 修改：从0.1秒改为1.0秒，避免超时
            )

            # ✅ 调试：输出响应状态
            if response.status_code != 200:
                logger.error(
                    f"[{self.camera_id}] HTTP API推送失败: "
                    f"status_code={response.status_code}, response={response.text}"
                )
            else:
                logger.debug(f"[{self.camera_id}] HTTP API推送成功")

        except requests.exceptions.Timeout:
            logger.error(f"[{self.camera_id}] HTTP API推送超时（1秒）")
        except requests.exceptions.RequestException as e:
            logger.error(f"[{self.camera_id}] HTTP API推送失败: {e}")
        except Exception as e:
            logger.error(f"[{self.camera_id}] 推送检测数据异常: {e}", exc_info=True)

    def _scale_bbox_to_original(self, bbox_640: List[float]) -> List[int]:
        """
        将检测框从 640x640 映射到原始分辨率

        Args:
            bbox_640: [x1, y1, x2, y2] in 640x640

        Returns:
            [x1, y1, x2, y2] in original resolution
        """
        # 如果没有获取到原始尺寸，返回原坐标（不映射）
        if self.zlm_original_width == 0 or self.zlm_original_height == 0:
            return [int(x) for x in bbox_640]

        # 计算缩放比例
        scale_x = self.zlm_original_width / 640.0
        scale_y = self.zlm_original_height / 640.0

        # 映射坐标
        return [
            int(bbox_640[0] * scale_x),
            int(bbox_640[1] * scale_y),
            int(bbox_640[2] * scale_x),
            int(bbox_640[3] * scale_y)
        ]
    
    def _draw_boxes_on_frame(self, frame: np.ndarray, detections: List[Dict], 
                            tracks: np.ndarray) -> np.ndarray:
        """
        在帧上绘制检测框和跟踪框（后端画框）
        
        Args:
            frame: 域图像
            detections: 检测结果列表
            tracks: 跟踪结果数组
        
        Returns:
            绘制后的帧图像
        """
        drawn_frame = frame.copy()
        
        # 读取配置
        det_color = tuple(self._det_box_cfg.get('color', [0, 255, 0]))
        det_thickness = self._det_box_cfg.get('thickness', 2)
        det_font_scale = self._det_box_cfg.get('font_scale', 0.5)
        det_label_color = tuple(self._det_box_cfg.get('label_color', [0, 255, 0]))
        
        track_color = tuple(self._track_box_cfg.get('color', [255, 0, 0]))
        track_thickness = self._track_box_cfg.get('thickness', 2)
        track_font_scale = self._track_box_cfg.get('font_scale', 0.5)
        track_label_color = tuple(self._track_box_cfg.get('label_color', [255, 0, 0]))
        
        traj_color = tuple(self._trajectory_cfg.get('color', [0, 255, 255]))
        traj_thickness = self._trajectory_cfg.get('thickness', 1)
        traj_point_size = self._trajectory_cfg.get('point_size', 2)
        traj_point_color = tuple(self._trajectory_cfg.get('point_color', [0, 0, 255]))
        
        # 绘制检测框
        if self._display.get('show_detection_boxes', True):
            for det in detections:
                bbox = det['bbox']
                class_name = det['class_name']
                confidence = det['confidence']
                
                cv2.rectangle(drawn_frame,
                             (int(bbox[0]), int(bbox[1])),
                             (int(bbox[2]), int(bbox[3])),
                             det_color, det_thickness)
                
                label = f"{class_name}: {confidence:.2f}"
                cv2.putText(drawn_frame, label,
                           (int(bbox[0]), int(bbox[1]) - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, det_font_scale, det_label_color, det_thickness)
        
        # 绘制跟踪框
        if self._display.get('show_track_boxes', True) and len(tracks) > 0:
            for track in tracks:
                track_id = int(track[4])
                bbox = track[:4]

                cv2.rectangle(drawn_frame,
                             (int(bbox[0]), int(bbox[1])),
                             (int(bbox[2]), int(bbox[3])),
                             track_color, track_thickness)

                # ✅ 修复：正确获取class_id和confidence
                # track数组格式：[x1, y1, x2, y2, track_id, class_id, confidence?]
                # - 实时跟踪（6列）：[x1, y1, x2, y2, track_id, class_id]
                # - 事件图片（7列）：[x1, y1, x2, y2, track_id, class_id, confidence]

                # 获取class_id
                if len(track) > 5:
                    class_id = int(track[5])  # ✅ 修正：索引5是class_id
                else:
                    # 回退：从active_tracks获取（实时跟踪）
                    track_info = self.active_tracks.get(track_id, {})
                    class_id = track_info.get('class_id', 0)

                class_name = self._get_class_name(class_id)

                # 获取confidence
                if len(track) > 6:
                    # ✅ 事件图片：tracks_array包含confidence（索引6）
                    confidence = float(track[6])
                else:
                    # ✅ 实时跟踪：从active_tracks或轨迹数据获取
                    track_info = self.active_tracks.get(track_id, {})
                    confidence = track_info.get('confidence', 0.0)

                    # 如果active_tracks中没有，尝试从轨迹数据获取
                    if confidence == 0.0 and track_id in self.track_trajectories:
                        confidence = self.track_trajectories[track_id].get('max_confidence', 0.0)

                label = f"ID: {track_id} {class_name}: {confidence:.2f}"
                cv2.putText(drawn_frame, label,
                           (int(bbox[0]), int(bbox[3]) + 20),
                           cv2.FONT_HERSHEY_SIMPLEX, track_font_scale, track_label_color, track_thickness)
        
        # 绘制轨迹线
        if self._display.get('show_trajectories', True) and self.enable_trajectory:
            # 使用 list() 快照避免并发修改（异步线程可能同时 pop 字典）
            for track_id, trajectory_info in list(self.track_trajectories.items()):
                points = trajectory_info['points']
                hist_len = self._display.get('trajectory_history_length', 30)
                points = points[-hist_len:]
                
                if len(points) < 2:
                    continue
                
                for i in range(1, len(points)):
                    pt1 = (int(points[i-1]['x']), int(points[i-1]['y']))
                    pt2 = (int(points[i]['x']), int(points[i]['y']))
                    cv2.line(drawn_frame, pt1, pt2, traj_color, traj_thickness)
                
                # 绘制轨迹点
                for pt in points:
                    cv2.circle(drawn_frame,
                              (int(pt['x']), int(pt['y'])),
                              traj_point_size, traj_point_color, -1)
        
        return drawn_frame
    
    def _send_ai_stream_frame(self, frame: np.ndarray, detections: List[Dict], tracks: np.ndarray):
        """通过 WebSocket 发送画框后的帧（AI 视频流）
        
        Args:
            frame: BGR 图像帧
            detections: 检测结果列表
            tracks: 跟踪结果数组
        """
        if not self.ai_stream_enabled:
            return
            
        # 将帧数据放入队列，由单独线程处理（避免阻塞主解码循环）
        try:
            # 只传递原始帧和检测数据，绘图在后台线程完成
            frame_copy = frame.copy()  # 创建副本避免多线程访问冲突

            # ✅ 调试：计算帧哈希，追踪数据流
            frame_hash = hash(frame.tobytes())
            if self.frame_count % 100 == 0:
                print(f"[CameraWorker-{self.camera_id}] 入队帧哈希: {frame_hash}, shape: {frame.shape}")

            queue_item = {
                'frame': frame_copy,
                'detections': detections,
                'tracks': tracks,
                'task_id': self.task_id,  # 使用任务ID区分不同任务
                'camera_id': self.camera_id,
                'frame_hash': frame_hash  # ✅ 新增：帧哈希用于调试
            }
            # 如果队列满，则丢弃旧帧（非阻塞）
            try:
                self.ai_stream_queue.put_nowait(queue_item)
            except queue.Full:
                # 队列满了，丢弃这一帧
                pass
        except Exception as e:
            if self.frame_count % 100 == 0:
                print(f"[CameraWorker-{self.camera_id}] AI 流入队错误: {e}")

    def _start_ai_stream_thread(self):
        """启动 AI 流处理线程"""
        if self.ai_stream_thread is None:
            self.ai_stream_stop_event.clear()
            self.ai_stream_thread = threading.Thread(target=self._ai_stream_worker, daemon=True)
            self.ai_stream_thread.start()

    def _ai_stream_worker(self):
        """AI 流处理工作线程"""
        import requests  # 只需要动态导入 requests

        # 调试：线程启动时记录
        print(f"[CameraWorker-{self.camera_id}] AI流处理线程启动: task_id={self.task_id}, camera_id={self.camera_id}")

        while not self.ai_stream_stop_event.is_set():
            try:
                # 从队列获取帧数据（最多等待 0.1 秒）
                queue_item = self.ai_stream_queue.get(timeout=0.1)

                # 调试：每100帧打印一次队列项信息
                if hasattr(self, '_ai_stream_process_count'):
                    self._ai_stream_process_count += 1
                else:
                    self._ai_stream_process_count = 0

                if self._ai_stream_process_count % 100 == 0:
                    print(f"[CameraWorker-{self.camera_id}] 处理AI流队列项: task_id={queue_item.get('task_id')}, camera_id={queue_item.get('camera_id')}")

                # 绘制检测框和跟踪框
                drawn_frame = self._draw_boxes_on_frame(
                    queue_item['frame'],
                    queue_item['detections'],
                    queue_item['tracks']
                )

                # 注意：queue_item['frame'] 来自 preprocessor._last_rgb，已经是 RGB 格式
                # OpenCV绘制函数支持RGB格式，无需转换

                # ✅ 初始化帧计数器（确保在使用前已初始化）
                if not hasattr(self, '_ai_stream_frame_count'):
                    self._ai_stream_frame_count = 0

                # ✅ 调试：计算帧内容的哈希，确认数据唯一性
                frame_hash = hash(queue_item['frame'].tobytes())
                if self._ai_stream_frame_count % 100 == 0:
                    print(f"[CameraWorker-{self.camera_id}] 帧哈希: {frame_hash}, shape: {queue_item['frame'].shape}")

                # ========================================
                # 新增：还原到原始分辨率（视频文件模式）
                # ========================================
                # 如果是视频文件且有原始分辨率信息，将画框后的图片缩放回原始尺寸
                original_drawn_frame_size = drawn_frame.shape[:2]  # 记录原始尺寸（用于日志）

                if self.source_original_width > 0 and self.source_original_height > 0:
                    try:
                        # 将640x640的画框图片还原到原始分辨率（如864x362）
                        drawn_frame = cv2.resize(
                            drawn_frame,
                            (self.source_original_width, self.source_original_height),
                            interpolation=cv2.INTER_LINEAR  # 线性插值，适合放大
                        )

                        # 可选：每隔一定帧数打印一次（避免日志过多）
                        if hasattr(self, '_ai_stream_resize_count'):
                            self._ai_stream_resize_count += 1
                        else:
                            self._ai_stream_resize_count = 0

                        if self._ai_stream_resize_count % 100 == 0:
                            print(f"[CameraWorker-{self.camera_id}] AI流还原: {original_drawn_frame_size[1]}x{original_drawn_frame_size[0]} → {drawn_frame.shape[1]}x{drawn_frame.shape[0]}")

                    except Exception as e:
                        print(f"[CameraWorker-{self.camera_id}] [WARN] AI流还原缩放失败: {e}")

                # 转换为 JPEG（BGR 格式，cv2.imencode 默认期望 BGR）
                # ✅ 修复：使用全局锁保护 OpenCV 操作，避免多线程竞争
                with _opencv_lock:
                    _, jpeg_buffer = cv2.imencode('.jpg', drawn_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    jpeg_bytes = jpeg_buffer.tobytes()  # 立即拷贝，避免后续访问冲突

                # 转换为 base64
                jpeg_base64 = base64.b64encode(jpeg_bytes).decode('utf-8')

                # 调试日志：确认推送的帧尺寸
                # 属性已在前面初始化，直接递增
                self._ai_stream_frame_count += 1

                if self._ai_stream_frame_count % 100 == 0:
                    print(f"[CameraWorker-{self.camera_id}] 推送AI流: 尺寸={drawn_frame.shape[1]}x{drawn_frame.shape[0]}, JPEG大小={len(jpeg_base64)}bytes")

                # ✅ 调试：保存帧到本地文件（每100帧保存一次）
                # 移到 try 块外面，确保总是执行
                if self._ai_stream_frame_count % 100 == 0:
                    import os
                    # 使用队列项中的 camera_id，确保数据正确
                    debug_camera_id = queue_item['camera_id']
                    debug_dir = f"output/ai_stream_debug/{debug_camera_id}"
                    os.makedirs(debug_dir, exist_ok=True)
                    debug_path = f"{debug_dir}/frame_{self._ai_stream_frame_count}.jpg"
                    with open(debug_path, 'wb') as f:
                        f.write(jpeg_bytes)  # 使用预拷贝的 bytes
                    print(f"[CameraWorker-{self.camera_id}] 保存调试帧: {debug_path} (实际camera_id={debug_camera_id})")

                # 通过 HTTP 请求发送到主进程（由主进程通过 WebSocket 广播）
                try:
                    requests.post(
                        'http://127.0.0.1:5555/api/ai-stream/push',
                        json={
                            'task_id': queue_item['task_id'],  # 使用任务ID区分不同任务
                            'camera_id': queue_item['camera_id'],
                            'frame': jpeg_base64,
                            'timestamp': datetime.now().isoformat(),
                            'original_width': self.source_original_width,  # 新增：携带原始尺寸信息
                            'original_height': self.source_original_height
                        },
                        timeout=0.5  # 降低超时避免阻塞worker线程
                    )
                except Exception:
                    pass  # 忽略发送失败（主进程可能未启动）
                    
            except queue.Empty:
                # 队列为空，继续循环
                continue
            except Exception as e:
                print(f"[CameraWorker-{self.camera_id}] AI 流处理错误: {e}")
    
    def _emit_zone_event(self, track_id: int, zone_name: str, event_type, bbox: tuple, confidence: float):
        """
        发送 Zone 区域事件（新增方法）
        
        参考 Frigate 的 Zone 事件机制：
        - 区域进入事件（zone_enter）
        - 区域离开事件（zone_leave）
        
        Args:
            track_id: 跟踪 ID
            zone_name: 区域名称
            event_type: 事件类型（ZoneEventType.ENTER 或 ZoneEventType.LEAVE）
            bbox: 边界框 (x1, y1, x2, y2)
            confidence: 置信度
        """
        if not self.event_output:
            return
        
        # 构造事件数据
        event_data = {
            "event_type": f"zone_{event_type.value}",
            "camera_id": self.camera_id,
            "track_id": track_id,
            "zone_name": zone_name,
            "bbox": list(bbox),
            "confidence": confidence,
            "timestamp": time.time(),
            "frame_seq": self.frame_count
        }
        
        # 通过事件线程异步发送（避免阻塞）
        try:
            self.event_output_thread.submit_event(
                event_type=f"zone/{zone_name}/{event_type.value}",
                data=event_data
            )

            # 打印日志（仅调试）
            # print(f"[CameraWorker-{self.camera_id}] Zone 事件: {zone_name} {event_type.value}, track_id={track_id}")

        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] Zone 事件发送失败: {e}")
    
    def cleanup(self):
        """清理资源"""
        # 刷新所有待处理的GIF事件（立即用缓存中已有帧生成，避免丢失事件）
        if self.pending_gifs:
            print(f"[CameraWorker-{self.camera_id}] 停止时刷新 {len(self.pending_gifs)} 个待处理GIF事件")
            for pending in self.pending_gifs:
                pending['generate_time'] = time.time()  # 立即到期
            self.pending_gifs_to_flush = self.pending_gifs
            self.pending_gifs = []
            for pending in self.pending_gifs_to_flush:
                try:
                    self._finalize_pending_gif(pending)
                except Exception as e:
                    print(f"[CameraWorker-{self.camera_id}] 停止时GIF生成失败: ID={pending.get('track_id')}, 错误={e}")
            # 等待后台线程完成（最多3秒）
            time.sleep(0.5)
        
        # 停止 AI 流处理线程
        if self.ai_stream_thread:
            self.ai_stream_stop_event.set()
            if self.ai_stream_thread.is_alive():
                self.ai_stream_thread.join(timeout=2.0)  # 最多等待2秒
            self.ai_stream_thread = None
            print(f"[CameraWorker-{self.camera_id}] AI 流处理线程已停止")
        
        # ========================================
        # 关闭GIF生成线程池（防止资源泄漏）
        # ========================================
        # 说明：
        # - shutdown(wait=True)：等待所有提交的任务完成
        # - wait=False：立即关闭（但已提交的任务会继续执行）
        # - 这里使用wait=True，确保所有GIF生成完成后再关闭
        # - 防止线程泄漏：线程池会正确清理所有线程资源
        if hasattr(self, 'gif_executor') and self.gif_executor:
            try:
                print(f"[CameraWorker-{self.camera_id}] 正在关闭GIF线程池...")
                self.gif_executor.shutdown(wait=True)  # 等待所有任务完成
                print(f"[CameraWorker-{self.camera_id}] GIF线程池已关闭")
            except Exception as e:
                print(f"[CameraWorker-{self.camera_id}] GIF线程池关闭失败: {e}")
                # 强制关闭
                try:
                    self.gif_executor.shutdown(wait=False)
                except:
                    pass
        
        # ========================================
        # 清理全局检测池资源
        # ========================================
        # 注意：全局检测进程池由 MultiCameraTaskExecutor 统一管理，这里只清理引用
        self.latest_detections = []

        if self.decoder:
            self.decoder.stop()
            self.decoder = None

        self.tracker = None
        # ✅ 删除：self.preprocessor（不再需要）

        if self.motion_detector:
            try:
                self.motion_detector.close()
            except:
                pass

        # ========================================
        # 停止事件输出线程（防止阻塞）
        # ========================================
        # 说明：
        # - 先停止事件线程，处理剩余事件
        # - 再关闭 event_output，释放资源
        # - 避免事件丢失和资源泄漏
        if hasattr(self, 'event_output_thread') and self.event_output_thread:
            try:
                print(f"[CameraWorker-{self.camera_id}] 正在停止事件输出线程...")
                self.event_output_thread.stop()

                # 等待线程结束（最多3秒）
                if self.event_output_thread.is_alive():
                    self.event_output_thread.join(timeout=3.0)

                print(f"[CameraWorker-{self.camera_id}] 事件输出线程已停止")
                print(f"[CameraWorker-{self.camera_id}]   统计信息: {self.event_output_thread.get_stats()}")

            except Exception as e:
                print(f"[CameraWorker-{self.camera_id}] 事件输出线程停止失败: {e}")

        if self.event_output:
            try:
                self.event_output.close()
            except:
                pass
        
        # ========================================
        # 新增：清理 Zone 和对象状态管理器
        # ========================================
        if self.zone_manager:
            self.zone_manager.clear_all_objects()
            print(f"[CameraWorker-{self.camera_id}] Zone 区域管理器已清理")
        
        if self.object_state_manager:
            self.object_state_manager.clear_all_objects()
            print(f"[CameraWorker-{self.camera_id}] 对象状态管理器已清理")
    
    def stop(self):
        """停止检测"""
        self.running = False
        print(f"[CameraWorker-{self.camera_id}] 正在停止...")

        # 停止解码器（优先停止，避免阻塞等待）
        if self.decoder:
            try:
                self.decoder.stop()
                print(f"[CameraWorker-{self.camera_id}] 解码器已停止")
            except Exception as e:
                print(f"[CameraWorker-{self.camera_id}] 停止解码器失败: {e}")

        # 停止 AI 流处理线程
        if self.ai_stream_thread:
            self.ai_stream_stop_event.set()
            if self.ai_stream_thread.is_alive():
                self.ai_stream_thread.join(timeout=2.0)  # 最多等待2秒
            self.ai_stream_thread = None
            print(f"[CameraWorker-{self.camera_id}] AI 流处理线程已停止")


class MultiCameraTaskExecutor:
    """多摄像机任务执行器 - 一个任务同时检测多个摄像机"""
    
    def __init__(self, task_config: Dict, cameras: List[Dict]):
        """
        初始化多摄像机任务执行器
        
        Args:
            task_config: 任务配置（检测算法配置）
            cameras: 摄像机列表 [{camera_id, rtsp_url, ...}]
        """
        self.task_config = task_config
        self.cameras = cameras
        
        self.task_id = task_config.get('id')
        self.task_name = task_config.get('task_name', 'unknown')
        self.algorithm = task_config.get('algorithm', 'yolov11')
        self.running = False
        # ========================================
        # 全局检测池模式（唯一模式）
        # ========================================
        self.tracker_class = None

        # 摄像机工作进程（多进程架构）
        self.worker_processes: Dict[str, Process] = {}

        print(f"[MultiCameraExecutor-{self.task_id}] 初始化")
        print(f"[MultiCameraExecutor-{self.task_id}] 任务名称: {self.task_name}")
        print(f"[MultiCameraExecutor-{self.task_id}] 算法: {self.algorithm}")
        print(f"[MultiCameraExecutor-{self.task_id}] 摄像机数量: {len(cameras)}")
        print(f"[MultiCameraExecutor-{self.task_id}] 使用全局检测进程池")
    
    def init_shared_components(self):
        """初始化共享组件（检测器）"""
        try:
            from src.algorithms import AlgorithmFactory
            from src.tracking.botsort import BoTSORTTracker

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
                    print(f"[MultiCameraExecutor-{self.task_id}] 从算法库读取配置:")
                    print(f"[MultiCameraExecutor-{self.task_id}]   模型路径: {algorithm_config['model_path']}")
                    print(f"[MultiCameraExecutor-{self.task_id}]   模型类型: {algorithm_config['model_type']}")
                    print(f"[MultiCameraExecutor-{self.task_id}]   标签文件: {algorithm_config['labels_file']}")
                conn.close()
            except Exception as e:
                print(f"[MultiCameraExecutor-{self.task_id}] 警告: 无法从算法库读取配置: {e}")

            # 从配置块中读取检测器参数（不再从任务表单独字段读取）
            detector_config_block = self.task_config.get('detector_config', {})

            # ========================================
            # 步骤2：合并配置（优先级：任务配置 > 算法库配置 > 默认值）
            # ========================================
            # 检测器配置
            detector_config = {
                'model_path': self.task_config.get('model_path') or algorithm_config.get('model_path', 'models/detection/yolov11n.onnx'),
                'target_classes': self.task_config.get('target_classes', ['person', 'car']),

                # 从 detector_config 配置块读取（不再从任务表单独字段读取）
                'conf_threshold': detector_config_block.get('conf_threshold', 0.25),
                'iou_threshold': detector_config_block.get('iou_threshold', 0.35),
                'min_box_area': detector_config_block.get('min_box_area', 50),

                # 检测器详细配置
                'backend': detector_config_block.get('backend', 'onnx_cuda'),
                'use_fp16': detector_config_block.get('use_fp16', True),
                'engine_cache_path': detector_config_block.get('engine_cache_path', './trt_cache'),
                'device_id': detector_config_block.get('device_id', 0),
                'gpu_mem_limit': detector_config_block.get('gpu_mem_limit', 6442450944),

                # TensorRT 配置（从 detector_config 配置块读取）
                'tensorrt_lib_path': detector_config_block.get('tensorrt_lib_path', ''),
                'tensorrt_engine_path': detector_config_block.get('tensorrt_engine_path', ''),

                # RKNN 配置（从 detector_config 配置块读取）
                'rknn_target': detector_config_block.get('rknn_target', 'rk3588'),
                'rknn_device_id': detector_config_block.get('rknn_device_id', ''),
                'rknn_core_mask': detector_config_block.get('rknn_core_mask', 0),

                # 标签文件路径（优先级：任务配置 > 算法库配置 > 默认值）
                'labels_file': detector_config_block.get('labels_file') or algorithm_config.get('labels_file', '')
            }
            
            print(f"[MultiCameraExecutor-{self.task_id}] 标签文件: {detector_config['labels_file'] or '未配置（使用COCO默认类别）'}")

            # ========================================
            # 新增：从标签文件加载类别名称
            # ========================================
            class_names = []
            labels_file = detector_config.get('labels_file', '')

            if labels_file and os.path.exists(labels_file):
                try:
                    with open(labels_file, 'r', encoding='utf-8') as f:
                        class_names = [line.strip() for line in f if line.strip()]
                    print(f"[MultiCameraExecutor-{self.task_id}] 从标签文件加载类别: {len(class_names)} 个类别")
                    if class_names:
                        print(f"[MultiCameraExecutor-{self.task_id}]   前5个类别: {class_names[:5]}")
                except Exception as e:
                    print(f"[MultiCameraExecutor-{self.task_id}] 加载标签文件失败: {e}")
            else:
                print(f"[MultiCameraExecutor-{self.task_id}] 未配置标签文件，将使用默认类别映射（class_0, class_1...）")

            # 将类别列表添加到检测器配置
            detector_config['class_names'] = tuple(class_names) if class_names else ()

            # ✅ 同时添加到 task_config，供 CameraWorker 使用
            self.task_config['class_names'] = tuple(class_names) if class_names else ()

            # ========================================
            # 算法名称映射逻辑
            # ========================================
            # algorithm_configs表的name字段可能是：'YOLOv11n', 'YOLOv8n', 'YOLOv5s'
            # AlgorithmFactory注册的名称是：'yolov11', 'yolov8', 'yolo'
            # 需要将algorithm_configs的名称映射到Factory注册的名称
            
            algorithm_name = self.algorithm
            
            # 算法名称映射表
            algorithm_mapping = {
                'YOLOv11n': 'yolov11',
                'YOLOv11s': 'yolov11',
                'YOLOv11m': 'yolov11',
                'YOLOv11l': 'yolov11',
                'YOLOv11x': 'yolov11',
                'YOLOv8n': 'yolov8',
                'YOLOv8s': 'yolov8',
                'YOLOv8m': 'yolov8',
                'YOLOv8l': 'yolov8',
                'YOLOv8x': 'yolov8',
                'YOLOv5n': 'yolo',
                'YOLOv5s': 'yolo',
                'YOLOv5m': 'yolo',
                'YOLOv5l': 'yolo',
                'YOLOv5x': 'yolo',
            }
            
            # 应用映射（如果映射表中有匹配项）
            factory_algorithm = algorithm_mapping.get(algorithm_name, algorithm_name)
            
            print(f"[MultiCameraExecutor-{self.task_id}] 算法名称映射: '{algorithm_name}' -> '{factory_algorithm}'")

            # ========================================
            # 初始化全局检测进程池（唯一模式）
            # ========================================
            print(f"[MultiCameraExecutor-{self.task_id}] 初始化全局检测进程池...")

            from src.detectors.global_detection_pool import get_global_detection_pool_manager
            from src.detectors.base import DetectorConfig

            # 构造 DetectorConfig 对象
            detector_config_obj = DetectorConfig(
                model_path=detector_config['model_path'],
                model_type=factory_algorithm,  # 使用映射后的算法名称
                backend=detector_config['backend'],
                conf_threshold=detector_config['conf_threshold'],
                iou_threshold=detector_config['iou_threshold'],
                min_box_area=detector_config['min_box_area'],
                use_fp16=detector_config['use_fp16'],
                device_id=detector_config['device_id'],
                gpu_mem_limit=detector_config['gpu_mem_limit'],
                tensorrt_lib_path=detector_config['tensorrt_lib_path'],
                tensorrt_engine_path=detector_config['tensorrt_engine_path'],
                rknn_target=detector_config['rknn_target'],
                rknn_device_id=detector_config['rknn_device_id'],
                rknn_core_mask=detector_config['rknn_core_mask'],
                class_names=detector_config['class_names'],  # 使用从标签文件加载的完整类别列表
                input_width=640,  # 默认输入尺寸
                input_height=640
            )

            # 获取全局检测进程池管理器（单例）
            global_pool_manager = get_global_detection_pool_manager()

            # 注册任务到全局检测进程池（避免重复加载模型）
            # 临时测试：默认 3 个检测器，验证多摄像机并发性能
            # TODO: 测试完成后改回 1，通过数据库配置覆盖
            num_detectors = self.task_config.get('num_detectors', 3)

            # ✅ 关键修复：提取所有摄像机ID，为每个摄像机创建独立队列（避免多摄像机队列混淆）
            camera_ids = [camera.get('camera_id') for camera in self.cameras if camera.get('camera_id')]

            # 注册任务，获取专属检测队列和结果队列
            task_queue, result_queues = global_pool_manager.register_task(
                task_id=str(self.task_id),
                model_type=factory_algorithm,  # 如 'yolov11n'
                model_path=detector_config['model_path'],
                detector_config=detector_config_obj.__dict__,  # 转换为字典
                num_detectors=num_detectors,
                camera_ids=camera_ids  # ✅ 新增：传入摄像机ID列表
            )

            # ✅ 保存队列和管理器引用
            self.detection_queue = task_queue

            # ✅ 关键修改：保存结果队列字典（每个摄像机独立队列）
            if isinstance(result_queues, dict):
                # 新架构：每个摄像机独立队列
                self.result_queues = result_queues  # 字典: {camera_id: result_queue}
                self.result_queue = None  # 不再使用任务级队列
                print(f"[MultiCameraExecutor-{self.task_id}] 全局检测进程池初始化成功（独立队列模式）")
                print(f"[MultiCameraExecutor-{self.task_id}]   摄像机数量: {len(camera_ids)}")
                print(f"[MultiCameraExecutor-{self.task_id}]   队列映射: {list(result_queues.keys())}")
            else:
                # 向后兼容：任务级队列（单摄像机或旧代码）
                self.result_queue = result_queues  # 单个队列
                self.result_queues = None
                print(f"[MultiCameraExecutor-{self.task_id}] 全局检测进程池初始化成功（共享队列模式）")
            self.global_pool_manager = global_pool_manager

            print(f"[MultiCameraExecutor-{self.task_id}] 全局检测进程池初始化成功")
            print(f"[MultiCameraExecutor-{self.task_id}]   模型类型: {factory_algorithm}")
            print(f"[MultiCameraExecutor-{self.task_id}]   检测器数量: {num_detectors}")
            print(f"[MultiCameraExecutor-{self.task_id}]   任务ID: {self.task_id}")
            print(f"[MultiCameraExecutor-{self.task_id}]   后端: {detector_config['backend']}")
            print(f"[MultiCameraExecutor-{self.task_id}]   FP16: {detector_config['use_fp16']}")

            # 获取跟踪器类
            self.tracker_class = BoTSORTTracker

            return True

        except Exception as e:
            print(f"[MultiCameraExecutor-{self.task_id}] 共享组件初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def start(self):
        """启动多摄像机检测"""
        self.running = True
        
        # 初始化共享组件
        if not self.init_shared_components():
            print(f"[MultiCameraExecutor-{self.task_id}] 初始化失败，无法启动")
            return False
        
        # 为每个摄像机创建工作进程
        for camera in self.cameras:
            camera_id = camera.get('camera_id')

            # ✅ 启动工作进程（多进程架构，绕过GIL限制）
            # 使用进程函数模式，避免pickle序列化CameraWorker对象
            process = Process(
                target=_camera_worker_process_wrapper,
                args=(camera, self.task_config, self.detection_queue, self.result_queues),  # ✅ 修复：传递result_queues字典
                name=f"CameraWorker-{camera_id}",
                daemon=True
            )
            process.start()
            self.worker_processes[camera_id] = process

            # 设置CPU亲和性：避免核心竞争（每个进程绑定独立核心）
            try:
                import psutil
                p = psutil.Process(process.pid)
                # 为每个CameraWorker分配独立核心（避免核心竞争）
                # 核心分配策略：CameraWorker使用核心2-11，检测进程池使用核心0-1
                worker_index = len(self.worker_processes) - 1
                core_start = 2 + worker_index * 2  # 每个Worker分配2个核心（物理+超线程）
                core_end = core_start + 2
                available_cores = list(range(core_start, min(core_end, 12)))  # 最多12核心
                if available_cores:
                    p.cpu_affinity(available_cores)
                    print(f"[MultiCameraExecutor-{self.task_id}] CameraWorker-{camera_id} 绑定到核心 {available_cores}")
            except Exception as e:
                print(f"[MultiCameraExecutor-{self.task_id}] 设置CPU亲和性失败: {e}")

            print(f"[MultiCameraExecutor-{self.task_id}] 启动摄像机工作进程: {camera_id}")
        
        print(f"[MultiCameraExecutor-{self.task_id}] 所有摄像机已启动")
        return True
    
    def run(self):
        """运行任务执行器（主循环）"""
        # 启动所有摄像机工作线程
        if not self.start():
            print(f"[MultiCameraExecutor-{self.task_id}] 启动失败，退出")
            return
        
        # 主循环：保持进程运行，监控进程状态
        print(f"[MultiCameraExecutor-{self.task_id}] 进入主循环")

        try:
            while self.running:
                # 检查所有工作进程是否存活
                alive_processes = 0
                for camera_id, process in self.worker_processes.items():
                    if process.is_alive():
                        alive_processes += 1
                    else:
                        print(f"[MultiCameraExecutor-{self.task_id}] 摄像机工作进程 {camera_id} 已停止")

                # 如果所有进程都停止，退出主循环
                if alive_processes == 0:
                    print(f"[MultiCameraExecutor-{self.task_id}] 所有工作进程已停止，退出主循环")
                    self.running = False
                    break
                
                # 每5秒检查一次进程状态
                time.sleep(5)
                
        except KeyboardInterrupt:
            print(f"[MultiCameraExecutor-{self.task_id}] 收到中断信号，停止任务")
            self.stop()
        except Exception as e:
            print(f"[MultiCameraExecutor-{self.task_id}] 主循环异常: {e}")
            self.stop()
        
        print(f"[MultiCameraExecutor-{self.task_id}] 任务执行器已退出")
    
    def stop(self):
        """停止所有摄像机检测"""
        self.running = False

        # 停止所有工作进程（通过终止进程）
        for camera_id, process in self.worker_processes.items():
            if process.is_alive():
                process.terminate()
                print(f"[MultiCameraExecutor-{self.task_id}] 终止摄像机工作进程: {camera_id}")

        # 等待进程结束
        for camera_id, process in self.worker_processes.items():
            process.join(timeout=5)
        
        # ========================================
        # 从全局检测进程池注销任务
        # ========================================
        print(f"[MultiCameraExecutor-{self.task_id}] 正在从全局检测进程池注销...")

        # 获取模型类型（用于注销）
        algorithm_name = self.task_config.get('algorithm', 'YOLOv11n')

        # 算法名称映射（与初始化时保持一致）
        algorithm_mapping = {
            'YOLOv11n': 'yolov11n',
            'YOLOv11s': 'yolov11s',
            'YOLOv11m': 'yolov11m',
            'YOLOv11l': 'yolov11l',
            'YOLOv11x': 'yolov11x',
            'YOLOv8n': 'yolov8n',
            'YOLOv8s': 'yolov8s',
            'YOLOv8m': 'yolov8m',
            'YOLOv8l': 'yolov8l',
            'YOLOv8x': 'yolov8x',
        }
        model_type = algorithm_mapping.get(algorithm_name, algorithm_name)

        # 注销任务
        self.global_pool_manager.unregister_task(
            task_id=str(self.task_id),
            model_type=model_type
        )

        print(f"[MultiCameraExecutor-{self.task_id}] 已从全局检测进程池注销")

        self.worker_processes.clear()

        print(f"[MultiCameraExecutor-{self.task_id}] 已停止")
    
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
            'camera_count': len(self.cameras),
            'cameras': camera_status
        }


# ========================================
# 任务调度器
# ========================================

class TaskScheduler:
    """任务调度器 - 管理多个并行任务"""
    
    def __init__(self):
        """初始化任务调度器"""
        self.executors: Dict[int, MultiCameraTaskExecutor] = {}
        self.running = False
        self._lock = threading.Lock()
        
        print("[TaskScheduler] 初始化")
    
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
            # 检查任务是否已运行
            if task_id in self.executors:
                print(f"[TaskScheduler] 任务 {task_id} 已在运行")
                return False
            
            # 创建执行器
            executor = MultiCameraTaskExecutor(task_config, cameras)
            
            # 启动
            if executor.start():
                self.executors[task_id] = executor
                print(f"[TaskScheduler] 任务 {task_id} 启动成功")
                return True
            else:
                print(f"[TaskScheduler] 任务 {task_id} 启动失败")
                return False
    
    def stop_task(self, task_id: int) -> bool:
        """停止任务"""
        with self._lock:
            if task_id not in self.executors:
                print(f"[TaskScheduler] 任务 {task_id} 未运行")
                return False
            
            executor = self.executors[task_id]
            executor.stop()
            del self.executors[task_id]
            
            print(f"[TaskScheduler] 任务 {task_id} 已停止")
            return True
    
    def stop_all(self):
        """停止所有任务"""
        with self._lock:
            for task_id, executor in self.executors.items():
                executor.stop()
            
            self.executors.clear()
            print("[TaskScheduler] 所有任务已停止")
    
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
    # ✅ 新增：配置子进程日志（与主进程使用相同的日志文件）
    import logging
    from logging.handlers import RotatingFileHandler
    import yaml
    from pathlib import Path

    try:
        # 加载系统配置
        config_path = Path(__file__).parent.parent.parent / 'config' / 'system_config.yaml'
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

        # 配置根logger（与主进程相同）
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.handlers.clear()

        # 文件handler（带轮转）
        file_handler = RotatingFileHandler(
            log_file, maxBytes=log_max_size, backupCount=log_backup_count, encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(file_handler)

        # 控制台handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(console_handler)

        print(f"[TaskExecutor] 子进程日志配置完成: {log_file}")
    except Exception as e:
        print(f"[TaskExecutor] 日志配置失败: {e}")

    if len(sys.argv) < 2:
        print("Usage: python -m src.tasks.task_executor <task_config_json>")
        sys.exit(1)

    # 解析任务配置
    try:
        task_config = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(f"[TaskExecutor] 配置解析失败: {e}")
        sys.exit(1)
    
    # 单任务模式（兼容 task_worker.py）
    # 如果没有指定摄像机列表，使用默认配置
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
        signal.signal(signal.SIGINT, lambda signum, frame: executor.stop())
        signal.signal(signal.SIGTERM, lambda signum, frame: executor.stop())
        
        # 主线程等待
        while executor.running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        executor.stop()


if __name__ == '__main__':
    main()