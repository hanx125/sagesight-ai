"""
批处理检测流水线（file / scheduled）

针对离线视频文件（或定时批量扫描）的检测，区别于实时流水线：
- 文件有终点：单文件处理到 EOF 自动退出，不无限循环等待帧。
- 支持文件列表：依次处理多个文件；file_loop=True 时循环（如实时源被当作文件循环）。
- 复用实时流水线的全部组件（解码器 / 运动检测 / 检测器 / 跟踪器 / region 工具 / 事件队列）。

scheduled 与 file 共用本流水线：两者都按文件列表跑一轮；scheduled 的"定时"由外层
调度器（run.py Scheduler）在 cron 命中时启动任务进程，本流水线本身不感知时间。
"""

import logging
import os
import time
from typing import Optional

import numpy as np

from src.detectors.region_utils import (
    area,
    create_tensor_input_hw,
    get_cluster_candidates,
    get_cluster_region,
    map_box_to_region,
    merge_yuv_planes,
    reduce_detections,
)
from src.motion.improved_motion import ImprovedMotionDetector
from src.tracking.norfair_tracker import NorfairTracker

logger = logging.getLogger(__name__)

# 最大 region 数量限制（避免过多 region 导致检测超时）
MAX_REGIONS = 20


def run_batch_pipeline(
    camera_id: str,
    task_id: str,
    decoder_config: dict,
    remote_detector,
    motion_detector: Optional[ImprovedMotionDetector],
    tracker: Optional[NorfairTracker],
    detected_objects_queue,
    frame_shape: tuple,
    model_size: tuple,
    detect_threshold: float,
    stop_event,
    target_classes: Optional[list] = None,
    backend: str = 'onnx_cuda',
    file_paths: Optional[list] = None,
    file_loop: bool = False,
):
    """
    批处理检测主循环

    对 file_paths 中的每个视频文件：创建解码器 → 逐帧检测 → 输出到事件队列 → EOF 退出。
    所有文件处理完后（且 file_loop=False）返回。

    Args:
        camera_id: 摄像机ID（与实时流水线一致，供事件处理关联通道/区域）
        task_id: 任务ID（用于共享内存命名）
        decoder_config: 解码器配置字典（含 camera_id/task_id/宽高等，video_file 会按文件覆盖）
        remote_detector: 远程检测器代理（RemoteObjectDetector）
        motion_detector: 运动检测器（None 表示关闭运动检测）
        tracker: NorFair 跟踪器（None 表示关闭跟踪）
        detected_objects_queue: 事件处理队列
        frame_shape: 帧形状 (height, width)
        model_size: 模型输入尺寸 (height, width)
        detect_threshold: 检测分数阈值
        stop_event: 停止事件
        target_classes: 目标类别过滤列表（可选）
        backend: 检测器后端
        file_paths: 待处理的视频文件路径列表
        file_loop: 是否循环处理文件列表（True 时无限循环，用于实时源当作文件）
    """
    proc_logger = logging.getLogger(f"pipeline.{camera_id}")
    proc_logger.info(
        f"批处理检管道启动: frame_shape={frame_shape}, "
        f"model_size={model_size}, threshold={detect_threshold}, "
        f"files={len(file_paths or [])}, file_loop={file_loop}"
    )

    file_paths = [p for p in (file_paths or []) if p]
    if not file_paths:
        proc_logger.warning("批处理：未配置文件路径，退出")
        return

    # backend=rknn 时启用 RGA 硬件预处理
    use_rga = (backend == 'rknn')
    if use_rga:
        proc_logger.info("RGA 硬件预处理已启用（backend=rknn）")

    use_cuda = False
    if not use_rga:
        try:
            from src.utils.platform_utils import get_platform
            if get_platform() == 'linux':
                use_cuda = True
                proc_logger.info("OpenCV CUDA 预处理已启用（platform=linux）")
        except ImportError:
            pass

    min_region = max(model_size)
    shm_name_prefix = f"sagesight_{task_id}_{camera_id}_block"

    total_files = len(file_paths)

    # 循环处理文件列表（file_loop=False 时处理一轮后退出）
    while not stop_event.is_set():
        for idx, file_path in enumerate(file_paths, start=1):
            if stop_event.is_set():
                break

            # 校验文件存在
            candidate = file_path.replace('file://', '') if file_path.startswith('file://') else file_path
            if not os.path.exists(candidate):
                proc_logger.warning(f"批处理文件不存在，跳过: {candidate}")
                continue

            proc_logger.info(f"批处理文件 ({idx}/{total_files}): {candidate}")
            _process_single_file(
                proc_logger, camera_id, task_id, decoder_config, candidate,
                remote_detector, motion_detector, tracker, detected_objects_queue,
                frame_shape, model_size, detect_threshold, stop_event,
                target_classes, use_rga, use_cuda, min_region, shm_name_prefix,
            )

        if not file_loop:
            break

    proc_logger.info("批处理流水线结束")


def _process_single_file(
    proc_logger, camera_id, task_id, decoder_config, file_path,
    remote_detector, motion_detector, tracker, detected_objects_queue,
    frame_shape, model_size, detect_threshold, stop_event,
    target_classes, use_rga, use_cuda, min_region, shm_name_prefix,
):
    """处理单个视频文件（解码 → 逐帧检测 → 输出 → EOF 退出）"""
    from src.decoders.video_decoder import VideoDecoder

    # 为当前文件创建解码器（文件模式：不重连，EOF 后结束）
    dcfg = dict(decoder_config)
    dcfg['video_file'] = file_path
    dcfg['rtsp_url'] = ''
    dcfg['reconnect'] = False

    decoder = VideoDecoder(dcfg)
    frame_count = 0
    idle_count = 0

    try:
        decoder.start()

        while not stop_event.is_set():
            frame_index = decoder.get_frame_index()

            if frame_index is None:
                # 无新帧：若文件已结束则退出，否则短暂等待
                if decoder.is_finished():
                    break
                idle_count += 1
                if idle_count > 50:  # 约 2.5s 无帧且未结束，视为文件结束兜底
                    if decoder.is_finished():
                        break
                time.sleep(0.05)
                continue

            idle_count = 0
            frame_count += 1

            header, y_plane, u_plane, v_plane = decoder.ring_buffer.read_frame_i420(
                frame_index.shm_block_id
            )
            if header is None or y_plane is None:
                continue

            frame_time = header.timestamp_ns / 1e9
            frame_name = str(header.sequence_num)

            # 运动检测（可选，用于 region 定位）
            if motion_detector is not None:
                motion_boxes = motion_detector.detect(y_plane)
                motion_box_tuples = [
                    tuple(m.bbox) if hasattr(m, 'bbox') else tuple(m)
                    for m in motion_boxes
                ]
            else:
                motion_boxes = []
                motion_box_tuples = []

            # 获取已跟踪对象框（用于 region 聚类）
            if tracker is not None:
                tracked_objects_dict = tracker.get_tracked_objects_dict()
                all_boxes = motion_box_tuples + list(tracker.untracked_object_boxes)
            else:
                tracked_objects_dict = {}
                all_boxes = list(motion_box_tuples)

            # region 生成
            regions = []
            if all_boxes:
                cluster_candidates = get_cluster_candidates(frame_shape, min_region, all_boxes)
                for cluster in cluster_candidates:
                    region = get_cluster_region(frame_shape, min_region, cluster, all_boxes)
                    regions.append(region)
                if len(regions) > MAX_REGIONS:
                    regions = regions[:MAX_REGIONS]

            # 检测
            all_detections = []
            if motion_detector is None and tracker is None:
                # 全帧检测
                yuv_frame = merge_yuv_planes(y_plane, u_plane, v_plane, frame_shape)
                full_frame_region = (0, 0, frame_shape[1], frame_shape[0])
                tensor = create_tensor_input_hw(
                    yuv_frame, full_frame_region, model_size,
                    use_rga=use_rga, use_cuda=use_cuda,
                )
                detections = remote_detector.detect(tensor, detect_threshold)
                for label, score, box in detections:
                    if target_classes and label not in target_classes:
                        continue
                    abs_box = map_box_to_region(box, full_frame_region, model_size)
                    all_detections.append(
                        (label, score, abs_box, area(abs_box),
                         (abs_box[2] - abs_box[0]) / max(1, abs_box[3] - abs_box[1]),
                         list(full_frame_region))
                    )
            elif regions:
                yuv_frame = merge_yuv_planes(y_plane, u_plane, v_plane, frame_shape)
                for region in regions:
                    tensor = create_tensor_input_hw(
                        yuv_frame, region, model_size,
                        use_rga=use_rga, use_cuda=use_cuda,
                    )
                    detections = remote_detector.detect(tensor, detect_threshold)
                    for label, score, box in detections:
                        abs_box = map_box_to_region(box, region, model_size)
                        if target_classes and label not in target_classes:
                            continue
                        all_detections.append(
                            (label, score, abs_box, area(abs_box),
                             (abs_box[2] - abs_box[0]) / max(1, abs_box[3] - abs_box[1]),
                             list(region))
                        )

            # 合并去重
            reduced_detections = reduce_detections(frame_shape, all_detections) if all_detections else []

            # 跟踪更新
            if tracker is not None:
                tracker.match_and_update(frame_name, frame_time, reduced_detections)
                tracked_objects_dict = tracker.get_tracked_objects_dict()
            else:
                tracked_objects_dict = {}

            # 输出到事件处理进程
            detection_results = [
                {"label": d[0], "score": d[1], "box": list(d[2])}
                for d in reduced_detections
            ] if reduced_detections else []

            try:
                detected_objects_queue.put(
                    (
                        camera_id,
                        frame_time,
                        tracked_objects_dict,
                        motion_box_tuples,
                        regions,
                        frame_index.shm_block_id,
                        shm_name_prefix,
                        detection_results,
                        header.sequence_num,
                    ),
                    timeout=0.05,
                )
            except Exception:
                proc_logger.warning("事件队列满,丢弃本帧检测数据")

        proc_logger.info(f"文件处理完成: {file_path} ({frame_count} 帧)")

    except Exception as e:
        proc_logger.error(f"批处理单文件异常 {file_path}: {e}", exc_info=True)
    finally:
        try:
            decoder.stop()
        except Exception:
            pass