"""
摄像机检测管道主循环

核心流程（每帧执行）:
1. 解码: read_frame_i420 → (header, y_plane, u_plane, v_plane)
2. 运动检测: motion_detector.detect(y_plane) → motion_boxes
3. Region 生成: motion_boxes + tracked_object_boxes → 聚类 → regions
4. 检测: 对每个 region 同步检测（RemoteObjectDetector）
5. 合并去重: reduce_detections → 去重后的检测列表
6. 跟踪: NorfairTracker.match_and_update() → 更新跟踪状态
7. 输出: detected_objects_queue.put() → 事件处理进程

架构特点:
- 运动检测用于缩小检测区域（每帧都检测）,而非触发检测
- 帧连续（不跳帧）,跟踪轨迹不断裂
- 同步检测（当帧完成）,非异步流水线
- 检测单元是 region（运动框+跟踪框聚类）,非整帧
- 结果通过共享内存+Event传递,非队列返回RGB帧
- 事件处理在独立进程,非CameraWorker内
"""

import logging
import time
from typing import Optional

import numpy as np

from src.detectors.region_utils import (
    area,
    create_tensor_input,
    get_cluster_candidates,
    get_cluster_region,
    merge_yuv_planes,
    reduce_detections,
)
from src.motion.improved_motion import ImprovedMotionDetector
from src.tracking.norfair_tracker import NorfairTracker

logger = logging.getLogger(__name__)

# 最大 region 数量限制（避免过多 region 导致检测超时）
MAX_REGIONS = 20


def run_pipeline(
    camera_id: str,
    task_id: str,
    decoder,
    motion_detector: ImprovedMotionDetector,
    remote_detector,
    tracker: NorfairTracker,
    detected_objects_queue,
    frame_shape: tuple,
    model_size: tuple,
    detect_threshold: float,
    stop_event,
    ptz_metrics=None,
    target_classes: Optional[list] = None,
):
    """
    CameraWorker 主循环

    每帧执行: 解码 → 运动检测 → region生成 → 同步检测 → 跟踪 → 输出

    Args:
        camera_id: 摄像机ID
        task_id: 任务ID（用于共享内存命名）
        decoder: 视频解码器（保留不变,提供 get_frame_index 和 ring_buffer）
        motion_detector: 运动检测器（ImprovedMotionDetector）
        remote_detector: 远程检测器代理（RemoteObjectDetector）
        tracker: Norfair 跟踪器
        detected_objects_queue: 事件处理队列（输出8-元组）
        frame_shape: 帧形状 (height, width)
        model_size: 模型输入尺寸 (height, width)
        detect_threshold: 检测分数阈值
        stop_event: 停止事件
        ptz_metrics: PTZ 共享状态（可选）
        target_classes: 目标类别过滤列表（可选,只保留这些类别的检测）
    """
    proc_logger = logging.getLogger(f"pipeline.{camera_id}")
    proc_logger.info(
        f"检测管道启动: frame_shape={frame_shape}, "
        f"model_size={model_size}, threshold={detect_threshold}"
    )

    # 最小 region 尺寸（取模型宽高的最大值）
    min_region = max(model_size)

    # 构建 shm_name 前缀（用于事件进程回读帧）
    shm_name_prefix = f"sagesight_{task_id}_{camera_id}_block"

    # 性能统计
    frame_count = 0
    perf_stats = {
        "decode_ms": [],
        "motion_ms": [],
        "region_ms": [],
        "detect_ms": [],
        "track_ms": [],
        "total_ms": [],
    }
    perf_log_interval = 100

    # 首帧标志
    first_frame = True

    while not stop_event.is_set():
        try:
            cycle_start = time.time()

            # ========================================
            # 1. 解码（保留不变）
            # ========================================
            decode_start = time.time()
            frame_index = decoder.get_frame_index()

            if frame_index is None:
                time.sleep(0.05)
                continue

            header, y_plane, u_plane, v_plane = decoder.ring_buffer.read_frame_i420(
                frame_index.shm_block_id
            )

            if header is None or y_plane is None:
                proc_logger.warning("解码失败,跳过本帧")
                continue

            decode_time = (time.time() - decode_start) * 1000
            perf_stats["decode_ms"].append(decode_time)

            frame_time = header.timestamp_ns / 1e9
            frame_name = str(header.sequence_num)
            frame_count += 1

            if first_frame:
                proc_logger.info(
                    f"首帧接收: seq={header.sequence_num}, "
                    f"block_id={frame_index.shm_block_id}, "
                    f"shape={y_plane.shape}"
                )
                first_frame = False

            # ========================================
            # 2. 运动检测（每帧执行,用于缩小检测区域）
            # ========================================
            motion_start = time.time()
            motion_boxes = motion_detector.detect(y_plane)
            motion_time = (time.time() - motion_start) * 1000
            perf_stats["motion_ms"].append(motion_time)

            # 提取 bbox 元组列表（MotionRegion 不可下标，region_utils 需要可下标的 box）
            motion_box_tuples = [
                tuple(m.bbox) if hasattr(m, 'bbox') else tuple(m)
                for m in motion_boxes
            ]

            # ========================================
            # 3. Region 生成（motion_boxes + tracked_object_boxes 聚类）
            # ========================================
            region_start = time.time()

            # 获取已跟踪对象的框
            tracked_objects_dict = tracker.get_tracked_objects_dict()
            tracked_object_boxes = [
                obj["box"] for obj in tracked_objects_dict.values() if obj.get("box")
            ]

            # 合并 motion_boxes 和未匹配的跟踪框
            all_boxes = motion_box_tuples + list(tracker.untracked_object_boxes)

            # 聚类生成 regions
            regions = []
            if all_boxes:
                cluster_candidates = get_cluster_candidates(
                    frame_shape, min_region, all_boxes
                )
                for cluster in cluster_candidates:
                    region = get_cluster_region(
                        frame_shape, min_region, cluster, all_boxes
                    )
                    regions.append(region)

            # 限制 region 数量
            if len(regions) > MAX_REGIONS:
                proc_logger.warning(
                    f"region 数量过多({len(regions)}),截断为 {MAX_REGIONS}"
                )
                regions = regions[:MAX_REGIONS]

            region_time = (time.time() - region_start) * 1000
            perf_stats["region_ms"].append(region_time)

            # ========================================
            # 4. 同步检测（对每个 region）
            # ========================================
            detect_start = time.time()

            all_detections = []

            if regions:
                # 合并 YUV 平面为完整 YUV 帧
                yuv_frame = merge_yuv_planes(
                    y_plane, u_plane, v_plane, frame_shape
                )

                for region in regions:
                    # 从 YUV 帧裁剪 region 并转为模型输入 tensor
                    tensor = create_tensor_input(yuv_frame, region, model_size)

                    # 同步检测（写输入SHM → queue.put → wait event → 读输出SHM）
                    detections = remote_detector.detect(tensor, detect_threshold)

                    # region 相对坐标 → 原图绝对坐标
                    for label, score, box in detections:
                        abs_box = (
                            int(box[0] + region[0]),
                            int(box[1] + region[1]),
                            int(box[2] + region[0]),
                            int(box[3] + region[1]),
                        )

                        # 目标类别过滤
                        if target_classes and label not in target_classes:
                            continue

                        box_area = area(abs_box)
                        box_w = abs_box[2] - abs_box[0]
                        box_h = abs_box[3] - abs_box[1]
                        ratio = box_w / max(1, box_h)

                        all_detections.append(
                            (label, score, abs_box, box_area, ratio, list(region))
                        )

            detect_time = (time.time() - detect_start) * 1000
            perf_stats["detect_ms"].append(detect_time)

            # ========================================
            # 5. 合并去重（多 region 检测结果 NMS）
            # ========================================
            if all_detections:
                reduced_detections = reduce_detections(frame_shape, all_detections)
            else:
                reduced_detections = []

            # ========================================
            # 6. 跟踪（更新跟踪状态）
            # ========================================
            track_start = time.time()
            tracker.match_and_update(frame_name, frame_time, reduced_detections)
            track_time = (time.time() - track_start) * 1000
            perf_stats["track_ms"].append(track_time)

            # ========================================
            # 7. 输出到事件处理进程
            # ========================================
            tracked_objects_dict = tracker.get_tracked_objects_dict()

            # 构造精简的检测结果列表（仅传坐标、标签、分数，不含面积/比率/region）
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
                    ),
                    timeout=0.5,
                )
            except Exception:
                proc_logger.warning("事件队列满,丢弃本帧检测数据")

            # ========================================
            # 性能统计
            # ========================================
            total_time = (time.time() - cycle_start) * 1000
            perf_stats["total_ms"].append(total_time)

            if frame_count % perf_log_interval == 0:
                avg = lambda lst: sum(lst[-100:]) / max(1, len(lst[-100:]))
                proc_logger.info(
                    f"性能统计(近100帧 avg ms): "
                    f"decode={avg(perf_stats['decode_ms']):.1f}, "
                    f"motion={avg(perf_stats['motion_ms']):.1f}, "
                    f"region={avg(perf_stats['region_ms']):.1f}, "
                    f"detect={avg(perf_stats['detect_ms']):.1f}, "
                    f"track={avg(perf_stats['track_ms']):.1f}, "
                    f"total={avg(perf_stats['total_ms']):.1f}, "
                    f"regions={len(regions)}, "
                    f"detections={len(reduced_detections)}, "
                    f"tracked={len(tracked_objects_dict)}"
                )

        except KeyboardInterrupt:
            proc_logger.info("收到键盘中断,退出")
            break
        except Exception as e:
            proc_logger.error(f"管道异常: {e}", exc_info=True)
            time.sleep(0.1)

    proc_logger.info(f"检测管道已退出 (共处理 {frame_count} 帧)")
