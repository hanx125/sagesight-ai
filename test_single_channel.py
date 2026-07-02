"""
单通道检测测试工具

用于独立测试单个RTSP流或视频文件的检测性能。
不依赖Web管理平台，适合调试和性能验证。

架构：生产者 - 消费者模式（多进程）
┌─────────────────┐         ┌─────────────────┐
│   主进程        │         │   推理进程      │
│  (解码 + 显示)  │ ──────→ │   (GPU 推理)    │
│  CPU 核 1-4     │  Queue  │  CPU 核 5-6 + GPU│
└─────────────────┘         └─────────────────┘
      ↓                            ↓
  共享内存                    结果输出

优势：
- 多进程架构充分利用多核 CPU
- GPU 持续工作不空闲
- 实时显示检测框、轨迹和统计信息
- 支持纯后端模式（无显示窗口）
- 预期 FPS: 20-25

硬件配置：NVIDIA RTX 2070 (8GB) | CUDA 12.9 | Python 3.11 | 12 核 CPU
检测类别：person, car

使用方式：
- 带显示：python test_single_channel.py
- 纇后端：python test_single_channel.py --no-display
"""

import sys
import os
import time
import yaml
import signal
import logging
import multiprocessing as mp
from multiprocessing import Process, Queue, Value, Array
from pathlib import Path
import numpy as np
import cv2

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.decoders import VideoDecoder, DecoderConfig
from src.utils import open_shared_memory
from src.detectors import YOLODetector, DetectorConfig
from src.detectors.preprocessor import OptimizedPreprocessor
from src.tracking.botsort import BoTSORTTracker  # 使用 BoT-SORT 替代 ByteTrack
from src.detectors.motion_detector import MotionDetector  # 新增：运动检测器
from src.events import EventOutput  # 新增：事件输出模块


class DetectionResult:
    """检测结果数据结构"""
    def __init__(self):
        self.frame_count = 0
        self.tracks = []
        self.detection_count = 0


def draw_detections(image, tracks, target_classes, class_names, track_history=None, colors=None, motion_detector=None, font_scale=0.6, font_thickness=2, line_thickness=2):
    """
    在图像上绘制检测结果（带跟踪 ID）和轨迹线
    
    Args:
        image: 输入图像
        tracks: 当前帧跟踪结果 [N, 7] (x1, y1, x2, y2, track_id, score, cls_id)
        target_classes: 目标类别列表
        class_names: 所有类别名称
        track_history: 轨迹历史 dict {track_id: deque of [cx, cy, w, h]}
        colors: 类别颜色配置 dict（必须从配置文件传入）
        motion_detector: 运动检测器（用于融合检测结果）
        font_scale: 字体大小
        font_thickness: 字体粗细
        line_thickness: 线条粗细
    """
    # 使用传入的颜色配置（必须从配置文件传入）
    drawing_colors = colors if colors else {}
    
    # 绘制轨迹线（只显示当前活跃轨迹）
    if track_history is not None and len(track_history) > 0:
        for track_id, history in track_history.items():
            if len(history) < 2:
                continue

            # 检查轨迹是否在当前帧的检测中
            is_active = False
            track_color = (0, 255, 0)  # 默认绿色

            for track in tracks:
                if int(track[4]) == track_id:
                    is_active = True
                    cls_id = int(track[6])
                    cls_name = class_names[cls_id] if cls_id < len(class_names) else 'car'
                    track_color = drawing_colors.get(cls_name, (0, 255, 0))
                    break

            # 只绘制活跃轨迹的轨迹线（防止显示丢失的轨迹）
            if not is_active:
                continue

            # 绘制轨迹线
            points = []
            for bbox in history:
                cx, cy = int(bbox[0]), int(bbox[1])
                points.append((cx, cy))
            
            for i in range(len(points) - 1):
                cv2.line(image, points[i], points[i + 1], track_color, line_thickness)
    
    # 绘制检测框
    for det in tracks:
        x1, y1, x2, y2 = map(int, det[:4])
        track_id = int(det[4])
        score = det[5]
        cls_id = int(det[6])
        
        class_name = class_names[cls_id] if cls_id < len(class_names) else f"cls_{cls_id}"
        
        if class_name not in target_classes:
            continue
        
        # === 新增：运动检测融合 ===
        motion_boost = 0.0
        if motion_detector is not None and motion_detector.is_initialized:
            # 检查检测框是否在运动区域内
            bbox = (x1, y1, x2, y2)
            motion_boost = motion_detector.get_motion_confidence_boost(bbox)
            
            # 融合置信度（可选，这里只用于显示）
            adjusted_score = score + motion_boost
        else:
            adjusted_score = score
        
        color = drawing_colors.get(class_name, (0, 255, 0))

        # === 显示标签（根据跟踪状态调整格式）===
        # track_id=-1 表示检测结果（无跟踪），不显示跟踪 ID
        if track_id == -1:
            # 检测模式：只显示类别和置信度
            if motion_boost > 0:
                label = f"{class_name}: {adjusted_score:.2f} (M+{motion_boost:.2f})"
                color = tuple(min(255, c + 50) for c in color)  # 增加亮度
            else:
                label = f"{class_name}: {adjusted_score:.2f}"
        else:
            # 跟踪模式：显示类别、跟踪 ID 和置信度
            if motion_boost > 0:
                label = f"{class_name} #{track_id}: {adjusted_score:.2f} (M+{motion_boost:.2f})"
                color = tuple(min(255, c + 50) for c in color)  # 增加亮度
            else:
                label = f"{class_name} #{track_id}: {adjusted_score:.2f}"
        
        # 绘制框
        cv2.rectangle(image, (x1, y1), (x2, y2), color, line_thickness)
        
        # 绘制标签背景
        (text_w, text_h), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)
        cv2.rectangle(image, (x1, y1 - text_h - baseline), (x1 + text_w, y1), color, -1)
        
        # 绘制文字
        cv2.putText(image, label, (x1, y1 - baseline), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), font_thickness)
    
    return image


def draw_stats(image, fps, frame_count, detection_count, active_tracks):
    """在图像上绘制统计信息"""
    stats = [
        f"FPS: {fps:.1f}",
        f"Frames: {frame_count}",
        f"Total Detections: {detection_count}",
        f"Active Tracks: {active_tracks}",
        f"Avg: {detection_count/frame_count:.2f}/frame" if frame_count > 0 else "Detecting..."
    ]
    
    # 绘制背景
    y_offset = 30
    cv2.rectangle(image, (10, 10), (300, y_offset + len(stats) * 25), (0, 0, 0), -1)
    cv2.rectangle(image, (10, 10), (300, y_offset + len(stats) * 25), (255, 255, 255), 1)
    
    # 绘制文字
    for i, stat in enumerate(stats):
        y = y_offset + i * 25
        cv2.putText(image, stat, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    
    return image


def detector_worker(config, frame_queue, result_queue, running, debug_print_interval=50):
    """
    推理进程（消费者）

    职责：
    - 从共享内存读取 YUV 帧
    - 预处理（YUV → RGB → Tensor）
    - GPU 推理（TensorRT / CUDA 加速）
    - NMS 后处理
    - BoT-SORT 跟踪
    - 结果输出到队列

    Args:
        config: 配置字典
        frame_queue: 帧 ID 队列（轻量级，仅传递 frame_id）
        result_queue: 结果输出队列
        running: 运行标志
        debug_print_interval: 调试信息打印间隔（帧数）
    """
    logger = logging.getLogger("DetectorWorker")
    logger.setLevel(logging.INFO)
    
    try:
        log_path = Path("logs/detector_worker.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding='utf-8')
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        logger.addHandler(fh)
    except Exception as e:
        print(f"[DetectorWorker] 日志创建失败：{e}")
    
    logger.info("推理进程启动")
    
    try:
        # 打开共享内存（读取端）
        from src.utils.shared_memory import open_shared_memory
        shm = open_shared_memory(config['shm_name'])
        logger.info(f"共享内存已打开：{config['shm_name']}")
        
        # 创建预处理器
        preprocessor = OptimizedPreprocessor(config['width'], config['height'])
        logger.info(f"预处理器已创建：{config['width']}x{config['height']}")

        # 确定像素格式（用于选择读取和预处理方法）
        output_format = config.get('output_format', 'yuv420p')  # 默认YUV420p
        use_nv12 = (output_format == 'nv12')
        logger.info(f"像素格式：{output_format} (使用NV12路径：{use_nv12})")

        # 加载标签
        labels_path = project_root / "models" / "coco_labels.txt"
        with open(labels_path, 'r', encoding='utf-8') as f:
            all_class_names = tuple([line.strip() for line in f.readlines()])
        
        # 创建检测器
        detector = YOLODetector(DetectorConfig(
            model_path=config['model_path'],
            model_type=config['model_type'],
            backend=config.get('backend', 'onnx_cuda'),
            tensorrt_engine_cache_path=config.get('tensorrt_engine_cache_path', './trt_cache'),
            use_fp16=config.get('use_fp16', True),
            conf_threshold=config['conf_threshold'],
            iou_threshold=config['iou_threshold'],
            class_names=all_class_names
        ))
        
        detector.load_model()
        # 获取实际使用的 Provider，确认 TensorRT 是否生效
        active_providers = detector.session.get_providers() if detector.session else ['unknown']
        logger.info(f"模型加载成功：{config['model_path']} (backend={config.get('backend', 'onnx_cuda')})")
        logger.info(f"实际使用的 Provider: {active_providers}")
        if 'TensorrtExecutionProvider' in active_providers:
            logger.info("[OK] TensorRT EP 已确认激活")
        else:
            logger.warning("[警告] TensorRT EP 未激活，可能回退到 CUDA 或 CPU")
        
        # === 使用 BoT-SORT 跟踪器（替代 ByteTrack）===
        tracker_cfg = config.get('tracker', {})
        tracker_enabled = tracker_cfg.get('enabled', True)  # 是否启用跟踪功能

        # 初始化 BoT-SORT 跟踪器（仅当启用时）
        tracker = None
        if tracker_enabled:
            botsort_cfg = tracker_cfg.get('botsort', {})

            track_thresh = botsort_cfg.get('track_thresh', 0.5)
            low_score_thresh = botsort_cfg.get('low_score_thresh', 0.1)
            match_thresh = botsort_cfg.get('match_thresh', 0.8)
            max_age = botsort_cfg.get('max_age', 30)
            n_init = botsort_cfg.get('n_init', 3)
            max_history_length = botsort_cfg.get('max_history_length', 30)

            # ReID 参数
            use_reid = botsort_cfg.get('use_reid', False)
            reid_model_type = botsort_cfg.get('reid_model_type', 'none')
            reid_model_path = botsort_cfg.get('reid_model_path', None)
            reid_thresh = botsort_cfg.get('reid_thresh', 0.3)

            # 设备参数
            device = botsort_cfg.get('device', 'cpu')
            half = botsort_cfg.get('half', False)

            tracker = BoTSORTTracker(
                track_thresh=track_thresh,
                low_score_thresh=low_score_thresh,
                match_thresh=match_thresh,
                max_age=max_age,
                n_init=n_init,
                max_history_length=max_history_length,
                use_reid=use_reid,
                reid_model_type=reid_model_type,
                reid_model_path=reid_model_path,
                reid_thresh=reid_thresh,
                device=device,
                half=half
            )

            logger.info(f"BoT-SORT 已初始化 (track_thresh={track_thresh}, low_thresh={low_score_thresh}, match_thresh={match_thresh}, max_age={max_age}, n_init={n_init}, use_reid={use_reid})")
        else:
            logger.info("跟踪功能已禁用（仅运行检测，不运行跟踪）")

        logger.info(f"调试打印间隔：{debug_print_interval} 帧")

        # 获取类别配置
        target_classes = config['target_classes']

        # 使用全局置信度阈值（不再使用 class_config）
        global_conf_threshold = config.get('conf_threshold', 0.25)
        logger.info(f"全局置信度阈值：{global_conf_threshold}")
        
        frame_count = 0
        detection_count = 0
        start_time = time.time()
        
        # 性能统计
        perf_stats = {
            'total_time': 0.0,
            'shm_time': 0.0,
            'preprocess_time': 0.0,
            'infer_time': 0.0,
            'postprocess_time': 0.0,
            'track_time': 0.0,
        }
        
        # 主循环
        while running.value:
            try:
                loop_start = time.time()
                
                # 从队列获取帧 ID（阻塞）
                try:
                    frame_id = frame_queue.get(timeout=1.0)
                except:
                    if not running.value:
                        logger.info("收到退出信号，推理进程结束")
                        break
                    continue
                
                # 从共享内存读取 YUV 帧
                t0 = time.time()
                try:
                    if use_nv12:
                        # NV12 格式：读取 Y 平面和 UV 交错平面
                        header, y, uv = shm.read_frame_nv12()
                    else:
                        # YUV420p 格式：读取 Y、U、V 三个平面
                        header, y, u, v = shm.read_frame()
                except Exception as e:
                    logger.warning(f"共享内存读取失败：{e}")
                    continue
                t1 = time.time()

                # 预处理：YUV → Tensor
                if use_nv12:
                    input_tensor = preprocessor.preprocess_nv12(y, uv)
                else:
                    input_tensor = preprocessor.preprocess(y, u, v)
                t2 = time.time()
                
                # 推理
                detections = detector.infer(input_tensor)
                t3 = time.time()
                
                frame_count += 1
                
                # 过滤目标类别（使用全局阈值）
                filtered_detections = []
                for det in detections:
                    if det.class_name not in target_classes:
                        continue
                    if det.confidence >= global_conf_threshold:
                        filtered_detections.append(det)
                t4 = time.time()
                
                # 转换为跟踪格式
                if len(filtered_detections) > 0:
                    det_array = np.array([
                        [
                            det.bbox[0], det.bbox[1], det.bbox[2], det.bbox[3],
                            det.confidence,
                            all_class_names.index(det.class_name) if det.class_name in all_class_names else 0
                        ]
                        for det in filtered_detections
                    ], dtype=np.float32)
                else:
                    det_array = np.zeros((0, 6), dtype=np.float32)

                # BoT-SORT 跟踪（仅当启用时）
                if tracker_enabled and tracker is not None:
                    tracks = tracker.update(det_array)
                    t5 = time.time()

                    # 收集轨迹历史（简化版，使用 numpy 向量化过滤）
                    track_history = {}
                    for track in tracker.tracked_tracks:
                        if track.consecutive_misses == 0 and len(track.history) >= 2:
                            hist = np.array(track.history)
                            valid_mask = (
                                (hist[:, 0] >= -500) & (hist[:, 1] >= -500) &
                                (hist[:, 0] < 2000) & (hist[:, 1] < 2000) &
                                (hist[:, 2] > 2) & (hist[:, 3] > 2)
                            )
                            valid_history = hist[valid_mask].tolist()
                            if len(valid_history) >= 2:
                                track_history[track.track_id] = valid_history
                else:
                    # 不启用跟踪：直接使用检测结果（转换为 tracks 格式）
                    # tracks 格式：[N, 7] (x1, y1, x2, y2, track_id, score, cls_id)
                    # 对于检测结果，track_id 设为 -1（表示无跟踪 ID）
                    if len(filtered_detections) > 0:
                        tracks = np.array([
                            [
                                det.bbox[0], det.bbox[1], det.bbox[2], det.bbox[3],
                                -1,  # track_id=-1 表示检测结果（无跟踪）
                                det.confidence,
                                all_class_names.index(det.class_name) if det.class_name in all_class_names else 0
                            ]
                            for det in filtered_detections
                        ], dtype=np.float32)
                    else:
                        tracks = np.zeros((0, 7), dtype=np.float32)

                    t5 = time.time()
                    track_history = {}  # 不启用跟踪，无轨迹历史
                
                # 发送结果到主进程（轻量结果）
                result_queue.put({
                    'frame_id': frame_id,
                    'tracks': tracks,
                    'track_history': track_history,
                    'class_names': all_class_names,
                    'detection_count': len(tracks)
                })
                
                detection_count += len(tracks)
                
                # 更新性能统计
                perf_stats['shm_time'] += t1 - t0
                perf_stats['preprocess_time'] += t2 - t1
                perf_stats['infer_time'] += t3 - t2
                perf_stats['postprocess_time'] += t4 - t3
                perf_stats['track_time'] += t5 - t4
                perf_stats['total_time'] += time.time() - loop_start
                
                # 定期记录 FPS 和性能分解
                if frame_count % 100 == 0:
                    elapsed = time.time() - start_time
                    fps = frame_count / elapsed if elapsed > 0 else 0
                    avg_total = perf_stats['total_time'] / frame_count * 1000
                    avg_shm = perf_stats['shm_time'] / frame_count * 1000
                    avg_pre = perf_stats['preprocess_time'] / frame_count * 1000
                    avg_inf = perf_stats['infer_time'] / frame_count * 1000
                    avg_post = perf_stats['postprocess_time'] / frame_count * 1000
                    avg_track = perf_stats['track_time'] / frame_count * 1000
                    logger.info(
                        f"FPS={fps:.1f} | 帧={frame_count} | "
                        f"总耗时={avg_total:.1f}ms | "
                        f"SHM={avg_shm:.1f}ms | "
                        f"预处理={avg_pre:.1f}ms | "
                        f"推理={avg_inf:.1f}ms | "
                        f"后处理={avg_post:.1f}ms | "
                        f"跟踪={avg_track:.1f}ms | "
                        f"目标={len(tracks)}"
                    )
                
            except Exception as e:
                logger.error(f"推理错误：{e}")
                time.sleep(0.001)
        
        # 清理
        shm.close()
        
        # 总结
        elapsed = time.time() - start_time
        logger.info("="*60)
        logger.info("推理进程总结")
        logger.info(f"运行时长：{elapsed:.1f}秒")
        logger.info(f"处理帧数：{frame_count}")
        logger.info(f"平均 FPS: {frame_count/elapsed if elapsed > 0 else 0:.1f}")
        logger.info("="*60)
        
    except Exception as e:
        logger.error(f"推理进程异常：{e}")
        running.value = 0


def main():
    """主函数"""
    print("\n" + "="*60)
    print("YOLO 视频检测通道（带实时显示）")
    print("硬件：NVIDIA RTX 2070 (8GB) | CUDA 12.9 | 12 核 CPU")
    print("架构：多进程（解码 + 显示 | GPU 推理）")
    print("="*60)
    
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser(description="YOLO 视频检测通道")
    parser.add_argument('--no-display', '-n', action='store_true', help='禁用显示窗口，纯后端运行')
    parser.add_argument('--display-enabled', '-d', type=str, default=None, 
                        help='是否启用显示（true/false），覆盖配置文件')
    args = parser.parse_args()
    
    # 加载配置
    print("\n[1/5] 加载配置文件...")
    decoder_cfg_path = project_root / "config" / "decoder_config.yaml"
    detector_cfg_path = project_root / "config" / "detector_config.yaml"
    tracker_cfg_path = project_root / "config" / "tracker_config.yaml"
    motion_cfg_path = project_root / "config" / "motion_config.yaml"
    test_cfg_path = project_root / "config" / "test_config.yaml"  # 测试工具专用配置

    with open(decoder_cfg_path, 'r', encoding='utf-8') as f:
        decoder_cfg = yaml.safe_load(f)

    with open(detector_cfg_path, 'r', encoding='utf-8') as f:
        detector_cfg = yaml.safe_load(f)

    with open(tracker_cfg_path, 'r', encoding='utf-8') as f:
        tracker_cfg = yaml.safe_load(f)

    with open(motion_cfg_path, 'r', encoding='utf-8') as f:
        motion_cfg = yaml.safe_load(f)

    # 加载测试工具专用配置
    with open(test_cfg_path, 'r', encoding='utf-8') as f:
        test_cfg = yaml.safe_load(f)

    # 加载事件输出配置
    event_output_cfg_path = project_root / "config" / "event_output.yaml"
    with open(event_output_cfg_path, 'r', encoding='utf-8') as f:
        event_output_cfg = yaml.safe_load(f)
    
    # 判断是视频文件还是 RTSP 流
    is_video_file = decoder_cfg['decoder'].get('video_file') is not None

    # TensorRT 配置
    backend = detector_cfg['detector'].get('backend', 'onnx_cuda')
    tensorrt_cfg = detector_cfg['detector'].get('tensorrt', {})
    tensorrt_engine_cache_path = tensorrt_cfg.get('engine_cache_path', './trt_cache')
    use_fp16 = tensorrt_cfg.get('use_fp16', True)
    tensorrt_lib_path = tensorrt_cfg.get('lib_path', '')

    # 设置 TensorRT lib 路径到环境变量（如果配置了）
    if tensorrt_lib_path:
        os.environ['TENSORRT_LIB_PATH'] = tensorrt_lib_path

    # 测试工具专用配置（从test_config.yaml加载）
    display_cfg = test_cfg.get('display', {})
    drawing_cfg = test_cfg.get('drawing', {})
    monitoring_cfg = test_cfg.get('monitoring', {})
    test_source_cfg = test_cfg.get('test_source', {})

    config = {
        'rtsp_url': decoder_cfg['decoder'].get('rtsp_url', ''),
        'video_file': decoder_cfg['decoder'].get('video_file', None),
        'rtsp_transport': decoder_cfg['decoder'].get('rtsp_transport', 'tcp'),
        'width': decoder_cfg['decoder']['width'],
        'height': decoder_cfg['decoder']['height'],
        'hwaccel': decoder_cfg['decoder']['hwaccel'],
        'output_format': decoder_cfg['decoder'].get('output_format', 'yuv420p'),  # 输出格式
        'model_path': detector_cfg['detector']['model_path'],
        'model_type': detector_cfg['detector']['model_type'],
        'backend': backend,
        'tensorrt_engine_cache_path': tensorrt_engine_cache_path,
        'use_fp16': use_fp16,
        'conf_threshold': detector_cfg['detector']['conf_threshold'],
        'iou_threshold': detector_cfg['detector']['iou_threshold'],
        'target_classes': detector_cfg['detector'].get('target_classes', ['person', 'car']),
        'tracker': tracker_cfg,  # 使用独立的跟踪配置
        'motion': motion_cfg,    # 使用独立的运动检测配置
        'shm_name': decoder_cfg['shared_memory']['name'],

        # 测试工具专用配置
        'display_enabled': display_cfg.get('enabled', True),
        'target_fps': display_cfg.get('target_fps', 30),
        'debug_print_interval': display_cfg.get('debug_print_interval', 50),
        'decoder_wait_time': display_cfg.get('decoder_wait_time', 2),

        'drawing_colors': drawing_cfg.get('colors', {}),
        'line_thickness': drawing_cfg.get('line_thickness', 2),
        'show_track_history': drawing_cfg.get('show_track_history', True),

        'monitoring_enabled': monitoring_cfg.get('enabled', True),
        'stats_interval': monitoring_cfg.get('stats_interval', 5)
    }
    
    # 判断是视频文件还是 RTSP 流
    if is_video_file:
        print(f"  视频文件：{config['video_file']}")
    else:
        print(f"  RTSP: {config['rtsp_url']}")
    print(f"  模型：{config['model_path']}")
    print(f"  推理后端：{config['backend']}")
    if config['backend'] == 'onnx_tensorrt':
        print(f"  TensorRT FP16：{'启用' if config['use_fp16'] else '禁用'}")
        print(f"  Engine 缓存：{config['tensorrt_engine_cache_path']}")
    print(f"  检测类别：{', '.join(config['target_classes'])}")
    print(f"  预期 FPS: 20-25")
    
    # 创建队列（frame_queue 只传递 frame_id，避免序列化 5MB tensor）
    frame_queue = Queue(maxsize=10)   # 轻量级队列，只需存 frame_id
    result_queue = Queue(maxsize=10)  # 结果队列，容量适中
    running = mp.Value('i', 1)
    
    # 读取调试打印间隔（从 tracker_config.yaml）
    debug_print_interval = tracker_cfg.get('debug_print_interval', 50)
    # 读取绘图配置启动推理进程
    print("\n[2/5] 启动推理进程...")

    detector_proc = Process(
        target=detector_worker,
        args=(config, frame_queue, result_queue, running, debug_print_interval),
        name="DetectorProcess"
    )
    detector_proc.start()
    print(f"  [OK] 推理进程已启动 (PID: {detector_proc.pid})")
    
    # 主进程：解码 + 显示
    print("\n[3/5] 初始化解码器...")
    
    # 根据配置创建 DecoderConfig（支持视频文件和 RTSP 流）
    if is_video_file:
        decoder_config = DecoderConfig(
            video_file=config['video_file'],
            width=config['width'],
            height=config['height'],
            hwaccel=config['hwaccel'],
            output_format=config['output_format'],  # 输出格式
            reconnect=False  # 视频文件不需要重连
        )
        print(f"  视频源：{config['video_file']}")
    else:
        decoder_config = DecoderConfig(
            rtsp_url=config['rtsp_url'],
            width=config['width'],
            height=config['height'],
            hwaccel=config['hwaccel'],
            output_format=config['output_format'],  # 输出格式
            rtsp_transport=config['rtsp_transport'],
            reconnect=True,
            reconnect_delay=5
        )
        print(f"  RTSP 源：{config['rtsp_url']}")
    
    decoder = VideoDecoder(decoder_config)
    
    if not decoder.start():
        print("[ERROR] 解码器启动失败")
        running.value = 0
        detector_proc.join()
        return 1
    
    print(f"  [OK] 解码器启动成功 (HW: {config['hwaccel']})")
    time.sleep(2)  # 等待缓冲
    
    # 打开共享内存
    print("\n[4/5] 打开共享内存...")
    shm = open_shared_memory(config['shm_name'])
    print(f"  [OK] 共享内存已打开")
    
    # 读取显示配置
    display_cfg = detector_cfg.get('display', {})
    window_title = display_cfg.get('window_title', "YOLO Detection - Press Q to quit")
    target_fps = display_cfg.get('target_fps', 30)
    display_interval = display_cfg.get('display_interval', 2)  # 每 N 帧显示一次，降低显示开销
    debug_print_interval = display_cfg.get('debug_print_interval', 50)
    
    # 确定显示启用状态（优先级：命令行参数 > 配置文件）
    # 1. 命令行参数 --no-display 优先
    # 2. 命令行参数 --display-enabled 其次
    # 3. 配置文件 display.enabled 最后
    display_enabled = display_cfg.get('enabled', True)  # 默认启用
    if args.no_display:
        display_enabled = False
    elif args.display_enabled is not None:
        display_enabled = args.display_enabled.lower() == 'true'
    
    # 读取绘图配置（仅在启用显示时需要）
    font_scale = 0.6
    font_thickness = 2
    line_thickness = 2
    show_track_history = True
    drawing_colors = {}
    if display_enabled:
        drawing_cfg = detector_cfg.get('drawing', {})
        font_scale = drawing_cfg.get('font_scale', 0.6)
        font_thickness = drawing_cfg.get('font_thickness', 2)
        line_thickness = drawing_cfg.get('line_thickness', 2)
        show_track_history = drawing_cfg.get('show_track_history', True)
        
        # 构建类别颜色字典
        for class_name, cfg in class_config.items():
            if class_name == 'defaults':
                continue
            if 'color' in cfg:
                drawing_colors[class_name] = tuple(cfg['color'])
        
        default_colors = drawing_cfg.get('colors', {})
        for class_name, color in default_colors.items():
            if class_name not in drawing_colors:
                drawing_colors[class_name] = tuple(color)
    
    print("\n[5/5] 开始实时检测...")
    print("="*60)
    if display_enabled:
        print(f"  显示模式：启用（每 {display_interval} 帧刷新一次）")
        print("提示：")
        print("  - 按 Q 键退出")
        print("  - 按 S 键保存当前帧")
    else:
        print("  显示模式：禁用（纯后端运行）")
        print("提示：")
        print("  - 按 Ctrl+C 退出")
    print("="*60)
    
    # 运动检测器（默认禁用，CPU 开销大）
    # motion_cfg 已在前面从 motion_config.yaml 加载
    use_motion_detection = motion_cfg.get('enabled', False)  # 默认禁用
    
    if use_motion_detection:
        motion_detector = MotionDetector(
            history=motion_cfg.get('history', 500),
            var_threshold=motion_cfg.get('var_threshold', 16.0),
            detect_shadows=motion_cfg.get('detect_shadows', False),
            min_area=motion_cfg.get('min_area', 500),
            blur_size=motion_cfg.get('blur_size', 5),
            morph_size=motion_cfg.get('morph_size', 5)
        )
        print(f"  [OK] 运动检测器已初始化 (history={motion_cfg.get('history', 500)}, min_area={motion_cfg.get('min_area', 500)})")
    else:
        motion_detector = None
        print(f"  [INFO] 运动检测已禁用（可在 config 中开启）")
    
    # 运动检测可视化开关
    show_motion_visualization = motion_cfg.get('show_visualization', False)
    
    # 事件输出器初始化
    event_output = None
    if event_output_cfg.get('event_output', {}).get('enabled', False):
        try:
            event_output = EventOutput(event_output_cfg['event_output'])
            print(f"  [OK] 事件输出器已初始化")
        except Exception as e:
            print(f"  [WARN] 事件输出器初始化失败: {e}")
            event_output = None
    else:
        print(f"  [INFO] 事件输出已禁用（可在 config/event_output.yaml 中开启）")
    
    frame_count = 0
    detection_count = 0
    start_time = time.time()
    saved_count = 0
    last_tracks = []
    last_track_history = {}
    last_class_names = []
    consecutive_read_failures = 0
    max_read_failures = 100
    last_sequence_num = -1
    consecutive_same_frames = 0
    max_same_frames = 100
    
    try:
        while True:
            # 从共享内存读取帧
            try:
                header, y, u, v = shm.read_frame()
                
                # 帧去重：检查序列号是否更新
                if header.sequence_num == last_sequence_num:
                    consecutive_same_frames += 1
                    if is_video_file and consecutive_same_frames >= max_same_frames:
                        print(f"\n[INFO] 视频文件已处理完毕")
                        break
                    time.sleep(0.001)
                    continue
                
                consecutive_same_frames = 0
                consecutive_read_failures = 0
                last_sequence_num = header.sequence_num
                frame_count += 1
                frame_id = header.sequence_num
            except Exception as e:
                consecutive_read_failures += 1
                if is_video_file and consecutive_read_failures >= max_read_failures:
                    print(f"\n[INFO] 视频文件已处理完毕")
                    break
                time.sleep(0.05)
                continue
            
            # YUV 转 BGR 用于显示
            yuv_frame = np.zeros((640 * 3 // 2, 640), dtype=np.uint8)
            yuv_frame[0:640, :] = y
            yuv_frame[640:800, :] = u.reshape((160, 640))
            yuv_frame[800:, :] = v.reshape((160, 640))
            bgr_frame = cv2.cvtColor(yuv_frame, cv2.COLOR_YUV2BGR_I420)
            
            # 发送帧 ID 到推理进程（轻量级，无 tensor 序列化开销）
            try:
                frame_queue.put(frame_id, block=False)
            except:
                pass
            
            # 获取最新检测结果（非阻塞，只取一个最新结果）
            try:
                while True:
                    result = result_queue.get(block=False)
                    last_tracks = result['tracks']
                    last_track_history = result['track_history']
                    last_class_names = result['class_names']
                    detection_count += result['detection_count']
            except:
                pass
            
            # 发送检测事件（如果事件输出器已初始化）
            if event_output is not None and len(last_tracks) > 0:
                for track in last_tracks:
                    track_id = int(track[4])
                    cls_id = int(track[6])
                    class_name = last_class_names[cls_id] if cls_id < len(last_class_names) else 'unknown'
                    confidence = float(track[5])
                    bbox = track[:4].tolist()  # [x1, y1, x2, y2]
                    
                    # 裁剪目标图片（可选）
                    x1, y1, x2, y2 = map(int, bbox)
                    if y2 > y1 and x2 > x1 and y1 >= 0 and x1 >= 0:
                        # 确保裁剪区域在帧范围内
                        h, w = bgr_frame.shape[:2]
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(w, x2), min(h, y2)
                        target_image = bgr_frame[y1:y2, x1:x2] if (y2 > y1 and x2 > x1) else None
                    else:
                        target_image = None
                    
                    # 发送事件
                    event_output.emit_detection(
                        track_id=track_id,
                        class_name=class_name,
                        confidence=confidence,
                        bbox=bbox,
                        image=target_image,
                        frame_id=frame_count,
                        extra_metadata={
                            'fps': fps,
                            'active_tracks': active_tracks
                        }
                    )
            
            # 计算 FPS
            elapsed = time.time() - start_time
            fps = frame_count / elapsed if elapsed > 0 else 0
            active_tracks = len(last_tracks) if len(last_tracks) > 0 else 0
            
            # 仅在启用显示时执行绘制操作
            if display_enabled:
                # 绘制检测结果
                if len(last_tracks) > 0:
                    display_frame = draw_detections(
                        bgr_frame,
                        last_tracks,
                        config['target_classes'],
                        last_class_names,
                        last_track_history if show_track_history else None,
                        colors=drawing_colors,
                        motion_detector=motion_detector,
                        font_scale=font_scale,
                        font_thickness=font_thickness,
                        line_thickness=line_thickness
                    )
                else:
                    display_frame = bgr_frame
                
                # 绘制统计信息
                display_frame = draw_stats(display_frame, fps, frame_count, detection_count, active_tracks)
            
            # 显示（按间隔刷新，降低显示开销）
            if display_enabled:
                if frame_count % display_interval == 0:
                    cv2.imshow(window_title, display_frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        print("\n用户退出")
                        break
                    elif key == ord('s'):
                        save_path = project_root / "output" / f"snapshot_{saved_count:03d}.jpg"
                        cv2.imwrite(str(save_path), display_frame)
                        print(f"  [OK] 已保存：{save_path}")
                        saved_count += 1
                else:
                    # 非显示帧仍需调用 waitKey(1) 处理窗口事件，但不刷新图像
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        print("\n用户退出")
                        break
            else:
                # 纯后端模式：定期打印统计信息
                if frame_count % 50 == 0:
                    print(f"  [INFO] FPS: {fps:.1f} | Frames: {frame_count} | Detections: {detection_count} | Active Tracks: {active_tracks}")
                    # 短暂休眠降低 CPU 占用
                    time.sleep(0.001)
    
    except KeyboardInterrupt:
        print("\n用户中断")
    
    finally:
        # 清理
        print("\n清理资源...")
        running.value = 0
        
        # 关闭事件输出器
        if event_output is not None:
            event_output.close()
        
        # 等待推理进程退出
        print("  [INFO] 等待推理进程退出...")
        detector_proc.join(timeout=3)
        if detector_proc.is_alive():
            print("  [WARN] 推理进程未响应，强制终止")
            detector_proc.terminate()
            detector_proc.join(timeout=1)
        else:
            print("  [OK] 推理进程已退出")
        
        decoder.stop()
        shm.close()
        cv2.destroyAllWindows()
    
    # 总结
    elapsed = time.time() - start_time
    print("\n" + "="*60)
    print("检测完成")
    print("="*60)
    print(f"运行时长：{elapsed:.1f}秒")
    print(f"处理帧数：{frame_count}")
    print(f"检测目标总数：{detection_count}")
    print(f"平均 FPS: {frame_count/elapsed if elapsed > 0 else 0:.1f}")
    print(f"保存图片数：{saved_count}")
    print("="*60)
    
    return 0


if __name__ == "__main__":
    def signal_handler(sig, frame):
        print("\n收到中断信号，正在退出...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n[ERROR] 程序异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
