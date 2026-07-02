"""
多摄像机任务执行器（实时检测，轮询检测待开发）

支持一个检测任务同时检测多个摄像机。
每个摄像机使用独立的解码器和跟踪器，共享同一个检测器（算法模型）。
所有配置从数据库读取，无需依赖配置文件。
"""

import sys
import os
import time
import json
import signal
import threading
import multiprocessing
import math
import queue
import cv2
import numpy as np
from datetime import datetime
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)


class CameraWorker:
    """单个摄像机的检测工作线程"""
    
    def __init__(self, camera_config: Dict, task_config: Dict, detector, tracker_class):
        """
        初始化摄像机工作线程
        
        Args:
            camera_config: 摄像机配置
            task_config: 任务配置（从数据库读取的完整配置）
            detector: 共享的检测器实例
            tracker_class: 跟踪器类
        """
        self.camera_config = camera_config
        self.task_config = task_config
        self.detector = detector
        self.tracker_class = tracker_class
        
        self.camera_id = camera_config.get('camera_id', 'unknown')
        self.rtsp_url = camera_config.get('rtsp_url', '')
        self.task_id = task_config.get('id')
        self.task_name = task_config.get('task_name', 'unknown')
        self.algorithm = task_config.get('algorithm', 'yolov11')
        self.running = False
        
        # 摄像机专属组件
        self.decoder = None
        self.tracker = None
        self.preprocessor = None
        self.motion_detector = None
        self.event_output = None
        self.gif_generator = None
        
        # 从配置中提取各模块配置
        self.decoder_cfg = task_config.get('decoder_config', {})
        self.detector_cfg = task_config.get('detector_config', {})
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
        
        # 轨迹数据配置
        self.trajectory_cfg = self.event_cfg.get('trajectory', {})
        self.enable_trajectory = self.trajectory_cfg.get('enabled', True)  # 是否启用轨迹数据
        self.trajectory_send_mode = self.trajectory_cfg.get('send_mode', 'complete')  # realtime | complete | both
        self.trajectory_max_points = self.trajectory_cfg.get('max_points', 100)  # 最大轨迹点数
        self.trajectory_send_interval = self.trajectory_cfg.get('send_interval', 10)  # 实时发送间隔（帧）
        
        # GIF 输出配置（从 event_config.gif 读取）
        self.gif_cfg = self.event_cfg.get('gif', {})
        self.generate_gif = self.gif_cfg.get('output_method', 'none') != 'none'  # 根据输出方法判断是否启用
        self.gif_max_frames = self.gif_cfg.get('max_frames', 30)  # GIF 最大帧数
        self.gif_fps = self.gif_cfg.get('fps', 5)  # GIF 帧率
        self.gif_max_width = self.gif_cfg.get('max_width', 320)  # GIF 最大宽度
        self.gif_max_height = self.gif_cfg.get('max_height', 240)  # GIF 最大高度
        self.gif_output_dir = self.gif_cfg.get('output_dir', 'output/gifs')  # GIF 输出目录
        self.gif_draw_trajectory = self.gif_cfg.get('draw_trajectory', True)  # 是否绘制轨迹
        
        # 关键帧采集配置（从 event_config.capture 读取）
        self.capture_cfg = self.event_cfg.get('capture', {})
        self.capture_strategy = self.capture_cfg.get('strategy', 'keyframe')  # full | cropped | keyframe | periodic
        self.capture_interval = self.capture_cfg.get('interval', 10)  # 周期性采集间隔
        self.change_threshold = self.capture_cfg.get('change_threshold', 50)  # 变化阈值（像素）
        self.save_full_on_start = self.capture_cfg.get('save_full_on_start', True)  # 首次出现保存完整帧
        self.save_full_on_end = self.capture_cfg.get('save_full_on_end', True)  # 目标消失保存完整帧
        
        # 统计
        self.frame_count = 0
        self.fps = 0.0
        self.last_fps_time = time.time()
        
        # AI 视频流 WebSocket 配置
        self.ai_stream_enabled = self._display.get('ai_stream_enabled', False)
        self.ai_stream_interval = self._display.get('ai_stream_interval', 2)
        self.ai_stream_frame_counter = 0
        
        # AI 流异步处理队列和线程
        self.ai_stream_queue = queue.Queue(maxsize=5)  # 限制队列大小防内存溢出
        self.ai_stream_thread = None
        self.ai_stream_stop_event = threading.Event()
        
        print(f"[CameraWorker-{self.camera_id}] 初始化")
        print(f"[CameraWorker-{self.camera_id}] RTSP: {self.rtsp_url}")
        print(f"[CameraWorker-{self.camera_id}] 任务名称: {self.task_name}")
        print(f"[CameraWorker-{self.camera_id}] 算法: {self.algorithm}")
        print(f"[CameraWorker-{self.camera_id}] 目标类别: {task_config.get('target_classes', [])}")
        print(f"[CameraWorker-{self.camera_id}] GIF输出方法: {self.gif_cfg.get('output_method', 'none')}")
        print(f"[CameraWorker-{self.camera_id}] GIF生成: {self.generate_gif}")
    
    def init_components(self):
        """初始化摄像机专属组件"""
        try:
            # 初始化解码器
            from src.decoders.video_decoder import VideoDecoder
            
            decoder_config = {
                'input_uri': self.rtsp_url,
                'width': self.decoder_cfg.get('width', 640),
                'height': self.decoder_cfg.get('height', 640),
                'hwaccel': self.decoder_cfg.get('hwaccel', 'dxva2'),
                'rtsp_transport': self.decoder_cfg.get('rtsp_transport', 'tcp'),
                'reconnect': self.decoder_cfg.get('reconnect', True),
                'reconnect_delay': self.decoder_cfg.get('reconnect_delay', 5),
                'buffer_size': self.decoder_cfg.get('buffer_size', 1048576),
                'frame_queue_size': self.decoder_cfg.get('frame_queue_size', 30),
                
                # 新架构配置参数（默认启用）
                'use_ring_buffer': self.decoder_cfg.get('use_ring_buffer', True),
                'ring_buffer_block_count': self.decoder_cfg.get('ring_buffer_block_count', 5),
                'index_queue_size': self.decoder_cfg.get('index_queue_size', 30)
            }
            
            self.decoder = VideoDecoder(decoder_config)
            self.decoder.start()
            
            print(f"[CameraWorker-{self.camera_id}] 解码器初始化成功")
            print(f"[CameraWorker-{self.camera_id}]   分辨率: {decoder_config['width']}x{decoder_config['height']}")
            print(f"[CameraWorker-{self.camera_id}]   硬件加速: {decoder_config['hwaccel']}")
            
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
            
            print(f"[CameraWorker-{self.camera_id}] 跟踪器初始化成功")
            print(f"[CameraWorker-{self.camera_id}]   track_thresh: {tracker_config['track_thresh']}")
            print(f"[CameraWorker-{self.camera_id}]   max_age: {tracker_config['max_age']}")
            
            # 初始化预处理器
            from src.detectors.preprocessor import OptimizedPreprocessor
            self.preprocessor = OptimizedPreprocessor()
            
            print(f"[CameraWorker-{self.camera_id}] 预处理器初始化成功")
            
            # 初始化运动检测器（可选）
            self._init_motion_detector()
            
            # 初始化事件输出器（可选）
            self._init_event_output()
            
            # 初始化 GIF 生成器（可选）
            self._init_gif_generator()
            
            return True
            
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] 初始化失败: {e}")
            return False
    
    def _init_motion_detector(self):
        """初始化运动检测器 - 从数据库配置读取"""
        if not self.motion_cfg.get('enabled', False):
            print(f"[CameraWorker-{self.camera_id}] 运动检测已禁用")
            return
        
        try:
            from src.motion.motion_detector import MotionDetector
            
            # 从数据库配置构建运动检测参数（完整参数列表）
            motion_config = {
                'history': self.motion_cfg.get('history', 500),
                'var_threshold': self.motion_cfg.get('var_threshold', 16.0),
                'detect_shadows': self.motion_cfg.get('detect_shadows', False),
                'min_area': self.motion_cfg.get('min_area', 500),
                'blur_size': self.motion_cfg.get('blur_size', 5),
                'morph_size': self.motion_cfg.get('morph_size', 5),
                'learning_rate': self.motion_cfg.get('learning_rate', -1),
                'motion_history_length': self.motion_cfg.get('motion_history_length', 10),
                'init_threshold': self.motion_cfg.get('init_threshold', 30),
                'foreground_threshold': self.motion_cfg.get('foreground_threshold', 0.3),
                'max_motion_regions': self.motion_cfg.get('max_motion_regions', 20),
                'confidence_boost': self.motion_cfg.get('confidence_boost', 0.15),
                'confidence_penalty': self.motion_cfg.get('confidence_penalty', 0.05)
            }
            
            self.motion_detector = MotionDetector(**motion_config)
            
            print(f"[CameraWorker-{self.camera_id}] 运动检测器初始化成功")
            print(f"[CameraWorker-{self.camera_id}]   history: {motion_config['history']}")
            print(f"[CameraWorker-{self.camera_id}]   min_area: {motion_config['min_area']}")
            print(f"[CameraWorker-{self.camera_id}]   motion_history_length: {motion_config['motion_history_length']}")
            print(f"[CameraWorker-{self.camera_id}]   init_threshold: {motion_config['init_threshold']}")
            
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] 运动检测器初始化失败: {e}")
            # 运动检测可选，失败不影响主流程
    
    def _init_event_output(self):
        """初始化事件输出器 - 从数据库配置读取"""
        if not self.event_cfg.get('enabled', False):
            print(f"[CameraWorker-{self.camera_id}] 事件输出已禁用")
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

            print(f"[CameraWorker-{self.camera_id}] 事件输出器初始化成功")
            print(f"[CameraWorker-{self.camera_id}]   图片本地保存: {event_config['image_output']['local_backup']}")
            print(f"[CameraWorker-{self.camera_id}]   图片保存目录: {event_config['image_output']['output_dir']}")
            print(f"[CameraWorker-{self.camera_id}]   图片输出方法: {event_config['image_output']['method']}")
            print(f"[CameraWorker-{self.camera_id}]   MinIO配置: {storage_cfg.get('minio', {}).get('enabled', False)}")
            print(f"[CameraWorker-{self.camera_id}]   FTP配置: {storage_cfg.get('ftp', {}).get('enabled', False)}")

        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] 事件输出器初始化失败: {e}")
            import traceback
            traceback.print_exc()
            # 事件输出可选
    
    def _init_gif_generator(self):
        """初始化 GIF 生成器"""
        if not self.generate_gif:
            print(f"[CameraWorker-{self.camera_id}] GIF生成已禁用")
            return
        
        try:
            from src.events.gif_generator import GIFGenerator
            
            gif_config = {
                'max_frames': self.gif_max_frames,
                'fps': self.gif_fps,
                'max_width': self.gif_max_width,
                'max_height': self.gif_max_height,
                'output_dir': self.gif_output_dir,
                'draw_trajectory': self.gif_draw_trajectory
            }
            
            self.gif_generator = GIFGenerator(gif_config)
            print(f"[CameraWorker-{self.camera_id}] GIF生成器初始化成功")
            
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] GIF生成器初始化失败: {e}")
            # GIF生成可选，失败不影响主流程
    
    def run(self):
        """运行摄像机检测"""
        self.running = True
        
        if not self.init_components():
            print(f"[CameraWorker-{self.camera_id}] 初始化失败，退出")
            return
        
        print(f"[CameraWorker-{self.camera_id}] 开始检测")

        # 启动 AI 流处理线程（如果启用）
        if self.ai_stream_enabled:
            self._start_ai_stream_thread()
            print(f"[CameraWorker-{self.camera_id}] AI 流处理线程已启动")

        # 从配置块读取 fps_limit
        fps_limit = self._display.get('target_fps', 0)

        # 运动检测模式标志（从任务配置读取,而不是从 motion_config 读取）
        motion_triggered_mode = self.task_config.get('motion_detection_enabled', False)
        print(f"[CameraWorker-{self.camera_id}] 调试: task_config.motion_detection_enabled = {motion_triggered_mode} (类型: {type(motion_triggered_mode)})")
        print(f"[CameraWorker-{self.camera_id}] 调试: task_config 包含的键 = {list(self.task_config.keys())}")
        if motion_triggered_mode:
            print(f"[CameraWorker-{self.camera_id}] 运动检测触发模式已启用")
            print(f"[CameraWorker-{self.camera_id}]   - 有运动目标时才启动检测")
            print(f"[CameraWorker-{self.camera_id}]   - 无运动目标时跳过检测（节约资源）")
        else:
            print(f"[CameraWorker-{self.camera_id}] 逐帧检测模式（运动检测已禁用）")
        
        # 统计计数器
        total_frames = 0
        motion_frames = 0
        detection_frames = 0
        skipped_frames = 0
        
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
                    time.sleep(0.01)
                    continue
                
                # 根据索引从环形缓冲区读取帧数据（零拷贝）
                read_frame_start = time.time()
                header, y_plane, uv_plane = self.decoder.ring_buffer.read_frame_nv12(frame_index.shm_block_id)
                read_frame_time = time.time() - read_frame_start
                
                self.frame_count += 1
                total_frames += 1
                
                # 首帧日志
                if total_frames == 1:
                    print(f"[CameraWorker-{self.camera_id}] 首帧接收成功（索引队列+环形缓冲区）")
                    print(f"[CameraWorker-{self.camera_id}]   块ID: {frame_index.shm_block_id}")
                    print(f"[CameraWorker-{self.camera_id}]   序列号: {frame_index.sequence_num}")
                    print(f"[CameraWorker-{self.camera_id}]   队列延迟: {frame_index.queue_delay_ms:.2f}ms")
                
                # ========================================
                # 预处理（NV12→RGB→归一化，生成BGR帧）
                # ========================================
                # 预处理始终执行（利用分辨率缩放、格式转换、降采样成果）
                preprocess_start = time.time()
                if self.preprocessor:
                    tensor = self.preprocessor.preprocess_nv12(y_plane, uv_plane)
                    bgr_frame = self.preprocessor._last_rgb.copy()
                else:
                    raise RuntimeError("预处理器未初始化")
                preprocess_time = time.time() - preprocess_start
                
                # ========================================
                # 运动检测触发模式（预处理后，先运动检测，再推理）
                # ========================================
                if motion_triggered_mode and self.motion_detector:
                    # 运动检测（使用预处理生成的BGR帧）
                    motion_detect_start = time.time()
                    motion_regions = self.motion_detector.detect(bgr_frame)
                    motion_detect_time = time.time() - motion_detect_start
                    
                    # 如果没有运动区域，跳过YOLO推理（节约GPU资源）
                    if not motion_regions or len(motion_regions) == 0:
                        skipped_frames += 1
                        
                        # 定期打印跳过统计
                        if skipped_frames % 100 == 0:
                            print(f"[CameraWorker-{self.camera_id}] 无运动目标，已跳过 {skipped_frames} 帧推理")
                        
                        # 继续下一帧（节约推理成本约20-30ms）
                        continue
                    
                    # 有运动目标，执行YOLO推理
                    motion_frames += 1
                    
                    # YOLO推理
                    detect_start = time.time()
                    result = self.detector.detect(tensor)
                    detections = result.get('detections', [])
                    detect_time = time.time() - detect_start
                    
                    detection_frames += 1
                    
                    # 运动检测融合（调整置信度）
                    if motion_regions and len(detections) > 0:
                        detections = self._fuse_motion_detection(detections, motion_regions)
                    
                    # 性能日志（每100帧打印一次）
                    if not hasattr(self, '_perf_counter'):
                        self._perf_counter = 0
                    self._perf_counter += 1
                    if self._perf_counter % 100 == 1 or self._perf_counter == 1:
                        print(f"[CameraWorker-{self.camera_id}] 性能分析（第{self._perf_counter}帧，运动触发）：")
                        print(f"  获取帧索引: {get_index_time*1000:.2f}ms")
                        print(f"  读取帧数据: {read_frame_time*1000:.2f}ms")
                        print(f"  预处理: {preprocess_time*1000:.2f}ms")
                        print(f"  运动检测: {motion_detect_time*1000:.2f}ms")
                        print(f"  推理: {detect_time*1000:.2f}ms")
                        total_time = time.time() - cycle_start
                        print(f"  总耗时: {total_time*1000:.2f}ms")
                        print(f"  FPS: {1000/(total_time*1000):.1f}")
                        print(f"  检测数量: {len(detections)}")
                    
                    # 定期打印检测结果
                    if detection_frames % 30 == 0:
                        print(f"[CameraWorker-{self.camera_id}] 已处理 {detection_frames} 帧（运动触发），检测到 {len(detections)} 个目标")
                        if len(detections) > 0:
                            for det in detections[:3]:
                                print(f"  - {det.get('class_name', 'unknown')}: 置信度 {det.get('confidence', 0):.2f}")
                
                # ========================================
                # 逐帧检测模式（预处理后，直接推理）
                # ========================================
                else:
                    # YOLO推理
                    detect_start = time.time()
                    result = self.detector.detect(tensor)
                    detections = result.get('detections', [])
                    detect_time = time.time() - detect_start
                    
                    detection_frames += 1
                    
                    # 性能日志（每100帧打印一次）
                    if not hasattr(self, '_perf_counter'):
                        self._perf_counter = 0
                    self._perf_counter += 1
                    if self._perf_counter % 100 == 1 or self._perf_counter == 1:
                        print(f"[CameraWorker-{self.camera_id}] 性能分析（第{self._perf_counter}帧，逐帧检测）：")
                        print(f"  获取帧索引: {get_index_time*1000:.2f}ms")
                        print(f"  读取帧数据: {read_frame_time*1000:.2f}ms")
                        print(f"  预处理: {preprocess_time*1000:.2f}ms")
                        print(f"  推理: {detect_time*1000:.2f}ms")
                        total_time = time.time() - cycle_start
                        print(f"  总耗时: {total_time*1000:.2f}ms")
                        print(f"  FPS: {1000/(total_time*1000):.1f}")
                        print(f"  检测数量: {len(detections)}")
                    
                    # 定期打印检测结果
                    if detection_frames % 30 == 0:
                        print(f"[CameraWorker-{self.camera_id}] 已处理 {detection_frames} 帧，检测到 {len(detections)} 个目标")
                        if len(detections) > 0:
                            for det in detections[:3]:
                                print(f"  - {det.get('class_name', 'unknown')}: 置信度 {det.get('confidence', 0):.2f}")
                
                # ========================================
                # 初始化 tracks 变量（避免未定义错误）
                # ========================================
                tracks = np.zeros((0, 7), dtype=np.float32)  # 默认为空数组
                
                # 跟踪（仅当启用时）
                tracking_enabled = self.task_config.get('tracking_enabled', True)
                if tracking_enabled and self.tracker and len(detections) > 0:
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
                    
                    # ========================================
                    # 跟踪结果处理：三路输出
                    # ========================================
                    # 1. WebSocket实时推送（供其他前端画框）
                    # 2. 后端画框（供本项目前端播放）
                    # 3. 事件信息生成（轨迹、关键帧、GIF）
                    
                    # 处理跟踪结果（仅当有跟踪目标时）
                    if len(tracks) > 0:
                        self._process_tracks(
                            tracks=tracks,
                            frame=bgr_frame,
                            frame_id=total_frames,
                            timestamp=datetime.now().isoformat()
                        )
                    
                # ========================================
                # 实时数据推送（WebSocket - 供其他前端画框）
                # ========================================
                if self.event_output and self.event_output.websocket_outputter:
                    self._send_realtime_data(
                        frame_id=total_frames,
                        detections=detections,
                        tracks=tracks
                    )
                
                # ========================================
                # 后端画框（供本项目前端播放）
                # ========================================
                # 如果 AI 视频流已启用，发送画框后的帧
                if self.ai_stream_enabled:
                    self.ai_stream_frame_counter += 1
                    if self.ai_stream_frame_counter >= self.ai_stream_interval:
                        self.ai_stream_frame_counter = 0
                        self._send_ai_stream_frame(bgr_frame, detections, tracks if self.tracker else [])
                
                # 显示（可选）
                if self._display.get('enabled', False):
                    self._display_frame(bgr_frame, detections, tracks if self.tracker else [])
                
                # FPS 控制
                if fps_limit > 0:
                    elapsed = time.time() - cycle_start
                    if elapsed < 1.0 / fps_limit:
                        time.sleep(1.0 / fps_limit - elapsed)
                
                # FPS 计算
                current_time = time.time()
                if current_time - self.last_fps_time >= 1.0:
                    self.fps = self.frame_count / (current_time - self.last_fps_time)
                    self.frame_count = 0
                    self.last_fps_time = current_time
                    print(f"[CameraWorker-{self.camera_id}] FPS: {self.fps:.2f}")
                
            except Exception as e:
                import traceback
                print(f"[CameraWorker-{self.camera_id}] 检测错误: {e}")
                print(f"[CameraWorker-{self.camera_id}] 错误堆栈:")
                traceback.print_exc()
                time.sleep(0.1)
        
        # 清理
        self.cleanup()
        print(f"[CameraWorker-{self.camera_id}] 检测结束")
    
    def _fuse_motion_detection(self, detections: List[Dict], motion_regions: List[Dict]) -> List[Dict]:
        """
        融合运动检测结果（调整置信度）
        
        Args:
            detections: 检测结果列表
            motion_regions: 运动区域列表
        
        Returns:
            融合后的检测结果列表
        """
        fused = []
        
        for det in detections:
            bbox = det.get('bbox', [])
            confidence = det.get('confidence', 0.0)
            
            # 检查检测框是否在运动区域内
            in_motion_region = False
            for region in motion_regions:
                if self._bbox_in_region(bbox, region):
                    in_motion_region = True
                    break
            
            # 调整置信度
            if in_motion_region:
                # 在运动区域内：提高置信度
                adjusted_conf = confidence + self.motion_cfg.get('confidence_boost', 0.15)
                adjusted_conf = min(adjusted_conf, 1.0)  # 上限为 1.0
            else:
                # 不在运动区域内：降低置信度
                adjusted_conf = confidence - self.motion_cfg.get('confidence_penalty', 0.05)
                adjusted_conf = max(adjusted_conf, 0.0)  # 下限为 0.0
            
            # 更新检测结果
            det['confidence'] = adjusted_conf
            det['motion_boost'] = in_motion_region
            
            fused.append(det)
        
        return fused
    
    def _bbox_in_region(self, bbox: List[float], region: Dict) -> bool:
        """检查边界框是否在运动区域内"""
        # 简单的中心点检查
        det_center_x = (bbox[0] + bbox[2]) / 2
        det_center_y = (bbox[1] + bbox[3]) / 2
        
        region_bbox = region.get('bbox', [0, 0, 0, 0])
        
        return (det_center_x >= region_bbox[0] and 
                det_center_x <= region_bbox[2] and
                det_center_y >= region_bbox[1] and 
                det_center_y <= region_bbox[3])
    
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
                        
                        cv2.putText(display_frame, f"ID: {track_id}",
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
        
        # 遍历所有跟踪目标
        for track in tracks:
            track_id = int(track[4])
            bbox = track[:4]
            confidence = float(track[5])  # score 在索引 5
            class_id = int(track[6]) if len(track) > 6 else 0  # cls_id 在索引 6
            
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
                    'class_id': class_id,
                    'confidence': confidence
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
                
                # 周期性采集（根据策略）- 始终采集，用于GIF生成
                if self.capture_strategy == 'periodic':
                    interval = self.capture_interval
                    last_capture_frame = self.track_frame_sequences.get(track_id, [{}])[-1].get('frame_id', 0)
                    
                    if frame_id - last_capture_frame >= interval:
                        self._capture_frame(
                            track_id=track_id,
                            frame=frame,
                            bbox=bbox,
                            frame_id=frame_id,
                            is_full_frame=False,
                            capture_reason='periodic'
                        )
        
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
                
                # 获取最后一帧（从帧序列中）
                frame_sequence = self.track_frame_sequences.get(disappeared_id, [])
                if frame_sequence:
                    last_frame_data = frame_sequence[-1]
                    last_frame = last_frame_data.get('image')
                    
                    # 使用 is not None 判断，避免numpy数组布尔错误
                    if last_frame is not None:
                        self._capture_frame(
                            track_id=disappeared_id,
                            frame=last_frame,
                            bbox=last_bbox,
                            frame_id=active_info['first_frame'] + len(frame_sequence),
                            is_full_frame=True,
                            capture_reason='track_end'
                        )
            
            # 生成GIF动画
            if self.generate_gif and self.gif_generator:
                self._generate_track_gif(disappeared_id)
            
            # 发送事件（完整事件数据，包含图片保存）
            if self.event_output:
                self._emit_track_event(
                    track_id=disappeared_id,
                    event_type='track_end',
                    frame_sequence=self.track_frame_sequences.get(disappeared_id, [])
                )
            
            # 清理数据
            del self.active_tracks[disappeared_id]
            if disappeared_id in self.track_frame_sequences:
                del self.track_frame_sequences[disappeared_id]
            if disappeared_id in self.track_trajectories:
                del self.track_trajectories[disappeared_id]
            
            print(f"[CameraWorker-{self.camera_id}] 跟踪目标消失: ID={disappeared_id}")
    
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
        
        # 实时发送轨迹更新（WebSocket）
        if self.trajectory_send_mode in ['realtime', 'both']:
            if len(trajectory['points']) % self.trajectory_send_interval == 0:
                if self.event_output and self.event_output.websocket_outputter:
                    self.event_output.send_trajectory_update(
                        track_id=track_id,
                        trajectory_points=trajectory['points']
                    )
    
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
        
        # 限制帧序列长度
        if len(self.track_frame_sequences[track_id]) >= self.gif_max_frames:
            # 移除最旧的帧
            self.track_frame_sequences[track_id].pop(0)
        
        # 采集帧数据
        frame_data = {
            'image': frame.copy() if is_full_frame else self._crop_frame(frame, bbox),
            'bbox': bbox.tolist(),
            'frame_id': frame_id,
            'timestamp': datetime.now().isoformat(),
            'is_full_frame': is_full_frame,
            'capture_reason': capture_reason
        }
        
        self.track_frame_sequences[track_id].append(frame_data)
        
        print(f"[CameraWorker-{self.camera_id}] 关键帧采集: ID={track_id}, 原因={capture_reason}, 帧类型={is_full_frame}")
    
    def _crop_frame(self, frame: np.ndarray, bbox: np.ndarray) -> np.ndarray:
        """
        裁剪帧（保留目标区域）
        
        Args:
            frame: 完整帧
            bbox: 边界框
        
        Returns:
            裁剪后的帧
        """
        h, w = frame.shape[:2]
        
        # 扩展边界框（保留上下文）
        margin_x = int((bbox[2] - bbox[0]) * 0.5)
        margin_y = int((bbox[3] - bbox[1]) * 0.5)
        
        x1 = max(0, int(bbox[0] - margin_x))
        y1 = max(0, int(bbox[1] - margin_y))
        x2 = min(w, int(bbox[2] + margin_x))
        y2 = min(h, int(bbox[3] + margin_y))
        
        return frame[y1:y2, x1:x2]
    
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
            gif_url = self.gif_generator.generate(
                track_id=track_id,
                frames=frame_sequence,
                output_filename=gif_filename
            )
            
            if gif_url:
                print(f"[CameraWorker-{self.camera_id}] GIF已生成: ID={track_id}, URL={gif_url}")
                
                # 保存GIF URL到轨迹数据
                if track_id in self.track_trajectories:
                    self.track_trajectories[track_id]['gif_url'] = gif_url
            else:
                print(f"[CameraWorker-{self.camera_id}] GIF生成失败: ID={track_id}")
            
        except Exception as e:
            print(f"[CameraWorker-{self.camera_id}] GIF生成失败: ID={track_id}, 错误={e}")
    
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

        # 获取GIF URL（如果有）
        gif_url = trajectory.get('gif_url', '')
        
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
            'gif_url': gif_url,
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
            
            # 在图片上绘制跟踪框和类别信息
            if last_frame_image is not None and len(frame_sequence) > 0:
                last_bbox = last_frame_data.get('bbox', [0, 0, 0, 0])
                tracks_array = np.array([[last_bbox[0], last_bbox[1], last_bbox[2], last_bbox[3], 
                                         track_id, trajectory.get('class_id', 0)]], dtype=np.float32)
                last_frame_image = self._draw_boxes_on_frame(
                    frame=last_frame_image,
                    detections=[],  # 事件图片只画跟踪框
                    tracks=tracks_array
                )

        # 发送事件（传入图片，会自动保存到本地）
        self.event_output.emit(
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
        # 优先从检测器获取（检测器有完整的类别列表）
        if self.detector and hasattr(self.detector, 'config') and hasattr(self.detector.config, 'class_names'):
            class_names = self.detector.config.class_names
            if class_names and class_id < len(class_names):
                return class_names[class_id]
        
        # 回退到 task_config
        class_names = self.task_config.get('class_names', [])
        if class_names and class_id < len(class_names):
            return class_names[class_id]
        return f'class_{class_id}'
    
    def _send_realtime_data(self, frame_id: int, detections: List[Dict],
                           tracks: np.ndarray):
        """
        发送实时数据到WebSocket（供其他前端画框）

        Args:
            frame_id: 帧ID
            detections: 检测结果列表
            tracks: 跟踪结果数组
        """
        if not self.event_output or not self.event_output.websocket_outputter:
            return

        # 构建检测框数据
        detection_boxes = []
        for det in detections:
            detection_boxes.append({
                'bbox': det['bbox'],
                'class_name': det['class_name'],
                'confidence': det['confidence'],
                'class_id': det['class_id']
            })

        # 构建跟踪框数据（使用len判断避免布尔错误）
        track_boxes = []
        if len(tracks) > 0:
            for track in tracks:
                track_boxes.append({
                    'bbox': track[:4].tolist(),
                    'track_id': int(track[4]),
                    'confidence': float(track[5]),  # score 在索引 5
                    'class_id': int(track[6]) if len(track) > 6 else 0  # cls_id 在索引 6
                })

        # 构建轨迹数据
        trajectory_data = {}
        for track_id, trajectory_info in self.track_trajectories.items():
            trajectory_data[track_id] = trajectory_info['points']

        # 发送到WebSocket
        self.event_output.send_detection_frame(
            camera_id=self.camera_id,
            frame_id=frame_id,
            detections=detection_boxes,
            tracks=track_boxes,
            trajectories=trajectory_data
        )
    
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
                
                label = f"ID: {track_id}"
                cv2.putText(drawn_frame, label,
                           (int(bbox[0]), int(bbox[3]) + 20),
                           cv2.FONT_HERSHEY_SIMPLEX, track_font_scale, track_label_color, track_thickness)
        
        # 绘制轨迹线
        if self._display.get('show_trajectories', True) and self.enable_trajectory:
            for track_id, trajectory_info in self.track_trajectories.items():
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
            queue_item = {
                'frame': frame_copy,
                'detections': detections,
                'tracks': tracks,
                'camera_id': self.camera_id
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
        import base64
        import requests
        
        while not self.ai_stream_stop_event.is_set():
            try:
                # 从队列获取帧数据（最多等待 0.1 秒）
                queue_item = self.ai_stream_queue.get(timeout=0.1)
                
                # 绘制检测框和跟踪框
                drawn_frame = self._draw_boxes_on_frame(
                    queue_item['frame'], 
                    queue_item['detections'], 
                    queue_item['tracks']
                )
                
                # 转换为 JPEG
                _, jpeg_buffer = cv2.imencode('.jpg', drawn_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                
                # 转换为 base64
                jpeg_base64 = base64.b64encode(jpeg_buffer.tobytes()).decode('utf-8')
                
                # 通过 HTTP 请求发送到主进程（由主进程通过 WebSocket 广播）
                try:
                    requests.post(
                        'http://127.0.0.1:5555/api/ai-stream/push',
                        json={
                            'camera_id': queue_item['camera_id'],
                            'frame': jpeg_base64,
                            'timestamp': datetime.now().isoformat()
                        },
                        timeout=1
                    )
                except Exception:
                    pass  # 忽略发送失败（主进程可能未启动）
                    
            except queue.Empty:
                # 队列为空，继续循环
                continue
            except Exception as e:
                print(f"[CameraWorker-{self.camera_id}] AI 流处理错误: {e}")
    
    def cleanup(self):
        """清理资源"""
        # 停止 AI 流处理线程
        if self.ai_stream_thread:
            self.ai_stream_stop_event.set()
            if self.ai_stream_thread.is_alive():
                self.ai_stream_thread.join(timeout=2.0)  # 最多等待2秒
            self.ai_stream_thread = None
            print(f"[CameraWorker-{self.camera_id}] AI 流处理线程已停止")
        
        if self.decoder:
            self.decoder.stop()
            self.decoder = None
        
        self.tracker = None
        self.preprocessor = None
        
        if self.motion_detector:
            try:
                self.motion_detector.close()
            except:
                pass
        
        if self.event_output:
            try:
                self.event_output.close()
            except:
                pass
    
    def stop(self):
        """停止检测"""
        self.running = False
        print(f"[CameraWorker-{self.camera_id}] 正在停止...")
        
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
        
        # 共享组件
        self.detector = None
        self.tracker_class = None
        
        # 摄像机工作线程
        self.camera_workers: Dict[str, CameraWorker] = {}
        self.worker_threads: Dict[str, threading.Thread] = {}
        
        print(f"[MultiCameraExecutor-{self.task_id}] 初始化")
        print(f"[MultiCameraExecutor-{self.task_id}] 任务名称: {self.task_name}")
        print(f"[MultiCameraExecutor-{self.task_id}] 算法: {self.algorithm}")
        print(f"[MultiCameraExecutor-{self.task_id}] 摄像机数量: {len(cameras)}")
    
    def init_shared_components(self):
        """初始化共享组件（检测器）"""
        try:
            from src.algorithms import AlgorithmFactory
            from src.tracking.botsort import BoTSORTTracker

            # 从配置块中读取检测器参数（不再从任务表单独字段读取）
            detector_config_block = self.task_config.get('detector_config', {})

            # 检测器配置
            detector_config = {
                'model_path': self.task_config.get('model_path', 'models/yolov11n.onnx'),
                'target_classes': self.task_config.get('target_classes', ['person', 'car']),

                # 从 detector_config 配置块读取（不再从任务表单独字段读取）
                'conf_threshold': detector_config_block.get('conf_threshold', 0.25),
                'iou_threshold': detector_config_block.get('iou_threshold', 0.35),
                'min_box_area': detector_config_block.get('min_box_area', 50),

                # 检测器详细配置
                'backend': detector_config_block.get('backend', 'onnx_tensorrt'),
                'use_fp16': detector_config_block.get('use_fp16', True),
                'engine_cache_path': detector_config_block.get('engine_cache_path', './trt_cache'),
                'device_id': detector_config_block.get('device_id', 0),
                'gpu_mem_limit': detector_config_block.get('gpu_mem_limit', 6442450944),
                
                # 标签文件路径（从 detector_config 配置块读取）
                'labels_file': detector_config_block.get('labels_file', '')
            }
            
            print(f"[MultiCameraExecutor-{self.task_id}] 标签文件: {detector_config['labels_file'] or '未配置（使用COCO默认类别）'}")

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

            # 创建共享检测器（使用映射后的算法名称）
            self.detector = AlgorithmFactory.create(factory_algorithm, detector_config)
            self.detector.initialize()

            print(f"[MultiCameraExecutor-{self.task_id}] 共享检测器初始化成功")
            print(f"[MultiCameraExecutor-{self.task_id}]   模型: {detector_config['model_path']}")
            print(f"[MultiCameraExecutor-{self.task_id}]   后端: {detector_config['backend']}")
            print(f"[MultiCameraExecutor-{self.task_id}]   FP16: {detector_config['use_fp16']}")
            print(f"[MultiCameraExecutor-{self.task_id}]   conf_threshold: {detector_config['conf_threshold']}")
            print(f"[MultiCameraExecutor-{self.task_id}]   iou_threshold: {detector_config['iou_threshold']}")

            # 获取跟踪器类
            self.tracker_class = BoTSORTTracker

            return True

        except Exception as e:
            print(f"[MultiCameraExecutor-{self.task_id}] 共享组件初始化失败: {e}")
            return False
    
    def start(self):
        """启动多摄像机检测"""
        self.running = True
        
        # 初始化共享组件
        if not self.init_shared_components():
            print(f"[MultiCameraExecutor-{self.task_id}] 初始化失败，无法启动")
            return False
        
        # 为每个摄像机创建工作线程
        for camera in self.cameras:
            camera_id = camera.get('camera_id')
            
            worker = CameraWorker(
                camera_config=camera,
                task_config=self.task_config,
                detector=self.detector,
                tracker_class=self.tracker_class
            )
            
            self.camera_workers[camera_id] = worker
            
            # 启动工作线程
            thread = threading.Thread(
                target=worker.run,
                name=f"CameraWorker-{camera_id}",
                daemon=True
            )
            thread.start()
            self.worker_threads[camera_id] = thread
            
            print(f"[MultiCameraExecutor-{self.task_id}] 启动摄像机工作线程: {camera_id}")
        
        print(f"[MultiCameraExecutor-{self.task_id}] 所有摄像机已启动")
        return True
    
    def run(self):
        """运行任务执行器（主循环）"""
        # 启动所有摄像机工作线程
        if not self.start():
            print(f"[MultiCameraExecutor-{self.task_id}] 启动失败，退出")
            return
        
        # 主循环：保持进程运行，监控线程状态
        print(f"[MultiCameraExecutor-{self.task_id}] 进入主循环")
        
        try:
            while self.running:
                # 检查所有工作线程是否存活
                alive_threads = 0
                for camera_id, thread in self.worker_threads.items():
                    if thread.is_alive():
                        alive_threads += 1
                    else:
                        print(f"[MultiCameraExecutor-{self.task_id}] 摄像机工作线程 {camera_id} 已停止")
                
                # 如果所有线程都停止，退出主循环
                if alive_threads == 0:
                    print(f"[MultiCameraExecutor-{self.task_id}] 所有工作线程已停止，退出主循环")
                    self.running = False
                    break
                
                # 每5秒检查一次线程状态
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
        
        # 停止所有工作线程
        for camera_id, worker in self.camera_workers.items():
            worker.stop()
        
        # 等待线程结束
        for camera_id, thread in self.worker_threads.items():
            thread.join(timeout=5)
        
        # 清理共享组件
        if self.detector:
            self.detector.close()
            self.detector = None
        
        self.camera_workers.clear()
        self.worker_threads.clear()
        
        print(f"[MultiCameraExecutor-{self.task_id}] 已停止")
    
    def get_status(self) -> Dict:
        """获取执行器状态"""
        camera_status = {}
        for camera_id, worker in self.camera_workers.items():
            camera_status[camera_id] = {
                'running': worker.running,
                'fps': worker.fps,
                'frame_count': worker.frame_count
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