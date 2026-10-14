"""
门控检测流水线（共享引擎）

以现有实时流水线（camera_pipeline.run_pipeline）为基础，增加"推理触发节奏"控制。
实时流水线每帧都跑模型推理；门控流水线按模式决定是否对本帧推理：

- polling（轮询检测）：
    每 polling_interval_frames 帧推理一次，其余帧跳过推理（reduced_detections 为空），
    但保留跟踪状态（tracker 以 max_age 维持轨迹），画面连续、轨迹不断裂，推理量降为 1/N。
- motion_gated（运动门控检测）：
    仅当本帧有运动（motion_boxes 非空）时推理；无运动时跳过推理。为防止长时间静止
    画面漏检停在原地/缓慢移动的目标，每 motion_gate_fallback_interval 帧强制补抽一次。

复用实时流水线的全部组件（解码器 / 运动检测 / 检测器 / 跟踪器 / region 工具 / 事件队列），
仅主循环的推理触发条件不同。解码、运动检测、跟踪、输出均持续执行。
"""

import logging
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


def run_gated_pipeline(
    camera_id: str,
    task_id: str,
    decoder,
    motion_detector: Optional[ImprovedMotionDetector],
    remote_detector,
    tracker: Optional[NorfairTracker],
    detected_objects_queue,
    frame_shape: tuple,
    model_size: tuple,
    detect_threshold: float,
    stop_event,
    ptz_metrics=None,
    target_classes: Optional[list] = None,
    backend: str = 'onnx_cuda',
    mode: str = 'polling',
    polling_interval_frames: int = 5,
    motion_gate_fallback_interval: int = 30,
):
    """
    CameraWorker 门控检测主循环

    每帧执行: 解码 → 运动检测(可选) → 按模式判断是否推理 → 推理帧做 region+检测+跟踪，
    非推理帧跳过推理（保留跟踪）→ 输出。

    Args:
        camera_id: 摄像机ID
        task_id: 任务ID（用于共享内存命名）
        decoder: 视频解码器
        motion_detector: 运动检测器（None 表示关闭运动检测）
        remote_detector: 远程检测器代理（RemoteObjectDetector）
        tracker: NorFair 跟踪器（None 表示关闭跟踪）
        detected_objects_queue: 事件处理队列
        frame_shape: 帧形状 (height, width)
        model_size: 模型输入尺寸 (height, width)
        detect_threshold: 检测分数阈值
        stop_event: 停止事件
        ptz_metrics: PTZ 共享状态（可选）
        target_classes: 目标类别过滤列表（可选）
        backend: 检测器后端
        mode: 门控模式（'polling' / 'motion_gated'）
        polling_interval_frames: 轮询抽样间隔（mode='polling'，默认 5）
        motion_gate_fallback_interval: 无运动兜底补抽间隔（mode='motion_gated'，默认 30）
    """
    proc_logger = logging.getLogger(f"pipeline.{camera_id}")
    proc_logger.info(
        f"门控检测管道启动: mode={mode}, frame_shape={frame_shape}, "
        f"model_size={model_size}, threshold={detect_threshold}"
    )

    # 参数校验：轮询间隔 / 兜底间隔至少为 1
    polling_interval_frames = max(1, int(polling_interval_frames or 1))
    motion_gate_fallback_interval = max(1, int(motion_gate_fallback_interval or 1))

    # backend=rknn 时启用 RGA 硬件预处理（RK3588 平台，失败自动回退 cv2）
    use_rga = (backend == 'rknn')
    if use_rga:
        proc_logger.info("RGA 硬件预处理已启用（backend=rknn）")

    # Linux（非 RK3588）启用 OpenCV CUDA 预处理
    use_cuda = False
    if not use_rga:
        try:
            from src.utils.platform_utils import get_platform
            if get_platform() == 'linux':
                use_cuda = True
                proc_logger.info("OpenCV CUDA 预处理已启用（platform=linux）")
        except ImportError:
            pass

    # 最小 region 尺寸（取模型宽高的最大值）
    min_region = max(model_size)

    # 构建 shm_name 前缀（用于事件进程回读帧）
    shm_name_prefix = f"sagesight_{task_id}_{camera_id}_block"

    # 性能统计
    frame_count = 0
    infer_count = 0  # 实际推理帧数（用于统计门控节省的推理量）
    skip_count = 0   # 跳过推理的帧数
    perf_stats = {
        "decode_ms": [],
        "motion_ms": [],
        "region_ms": [],
        "detect_ms": [],
        "track_ms": [],
        "total_ms": [],
        "regions": [],
        "detections": [],
        "tracked": [],
    }
    perf_log_interval = 100

    first_frame = True

    while not stop_event.is_set():
        try:
            cycle_start = time.time()

            # ========================================
            # 1. 解码
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
            # 2. 运动检测（可选，用于门控判断 + 缩小检测区域）
            # ========================================
            motion_start = time.time()
            if motion_detector is not None:
                motion_boxes = motion_detector.detect(y_plane)
                motion_box_tuples = [
                    tuple(m.bbox) if hasattr(m, 'bbox') else tuple(m)
                    for m in motion_boxes
                ]
            else:
                motion_boxes = []
                motion_box_tuples = []
            motion_time = (time.time() - motion_start) * 1000
            perf_stats["motion_ms"].append(motion_time)

            # ========================================
            # 3. 按模式判断本帧是否推理
            # ========================================
            if mode == 'polling':
                infer_this_frame = (frame_count % polling_interval_frames == 0)
            elif mode == 'motion_gated':
                # 无运动检测器时无法门控，退化为按兜底间隔轮询
                if motion_detector is None:
                    infer_this_frame = (frame_count % motion_gate_fallback_interval == 0)
                else:
                    infer_this_frame = bool(motion_box_tuples) or (
                        frame_count % motion_gate_fallback_interval == 0
                    )
            else:
                infer_this_frame = True

            # ========================================
            # 4. 推理帧：region 生成 + 检测；非推理帧跳过
            # ========================================
            region_start = time.time()
            all_detections = []
            regions = []

            if infer_this_frame:
                # 获取已跟踪对象的框（跟踪关闭时无框）
                if tracker is not None:
                    tracked_objects_dict = tracker.get_tracked_objects_dict()
                    tracked_object_boxes = [
                        obj["box"] for obj in tracked_objects_dict.values() if obj.get("box")
                    ]
                    # 合并 motion_boxes 和未匹配的跟踪框
                    all_boxes = motion_box_tuples + list(tracker.untracked_object_boxes)
                else:
                    tracked_objects_dict = {}
                    tracked_object_boxes = []
                    all_boxes = list(motion_box_tuples)

                # 聚类生成 regions
                if all_boxes:
                    cluster_candidates = get_cluster_candidates(
                        frame_shape, min_region, all_boxes
                    )
                    for cluster in cluster_candidates:
                        region = get_cluster_region(
                            frame_shape, min_region, cluster, all_boxes
                        )
                        regions.append(region)

                if len(regions) > MAX_REGIONS:
                    proc_logger.warning(
                        f"region 数量过多({len(regions)}),截断为 {MAX_REGIONS}"
                    )
                    regions = regions[:MAX_REGIONS]

                region_time = (time.time() - region_start) * 1000
                perf_stats["region_ms"].append(region_time)

                # 同步检测
                detect_start = time.time()

                if motion_detector is None and tracker is None:
                    # 运动检测和跟踪都关闭：直接全帧检测（绕过 regions）
                    yuv_frame = merge_yuv_planes(
                        y_plane, u_plane, v_plane, frame_shape
                    )
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
                    yuv_frame = merge_yuv_planes(
                        y_plane, u_plane, v_plane, frame_shape
                    )
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

                detect_time = (time.time() - detect_start) * 1000
                perf_stats["detect_ms"].append(detect_time)
                infer_count += 1
            else:
                # 非推理帧：跳过推理，统计跳过帧数
                skip_count += 1
                # 非推理帧仍需要 tracked_objects_dict 用于输出（跟踪状态由下面 tracker 维护）
                tracked_objects_dict = tracker.get_tracked_objects_dict() if tracker else {}

            # ========================================
            # 5. 合并去重（推理帧）
            # ========================================
            if all_detections:
                reduced_detections = reduce_detections(frame_shape, all_detections)
            else:
                reduced_detections = []

            # ========================================
            # 6. 跟踪（每个推理帧更新；非推理帧以空检测维持轨迹 max_age）
            # ========================================
            track_start = time.time()
            if tracker is not None:
                tracker.match_and_update(frame_name, frame_time, reduced_detections)
                tracked_objects_dict = tracker.get_tracked_objects_dict()
            else:
                tracked_objects_dict = {}
            track_time = (time.time() - track_start) * 1000
            perf_stats["track_ms"].append(track_time)

            # ========================================
            # 7. 输出到事件处理进程
            # ========================================
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
                        header.sequence_num,  # 帧序号（用于回读 shm 时校验块是否已被覆盖）
                    ),
                    timeout=0.05,
                )
            except Exception:
                proc_logger.warning("事件队列满,丢弃本帧检测数据")

            # ========================================
            # 性能统计
            # ========================================
            total_time = (time.time() - cycle_start) * 1000
            perf_stats["total_ms"].append(total_time)
            perf_stats["regions"].append(len(regions))
            perf_stats["detections"].append(len(reduced_detections))
            perf_stats["tracked"].append(len(tracked_objects_dict))

            if frame_count % perf_log_interval == 0:
                avg = lambda lst: sum(lst[-100:]) / max(1, len(lst[-100:]))
                infer_ratio = infer_count / max(1, frame_count) * 100
                proc_logger.info(
                    f"门控性能({mode}): 总帧={frame_count}, 推理={infer_count}"
                    f"({infer_ratio:.1f}%), 跳过推理={skip_count}, "
                    f"decode={avg(perf_stats['decode_ms']):.1f}ms, "
                    f"motion={avg(perf_stats['motion_ms']):.1f}ms, "
                    f"detect={avg(perf_stats['detect_ms']):.1f}ms, "
                    f"track={avg(perf_stats['track_ms']):.1f}ms, "
                    f"total={avg(perf_stats['total_ms']):.1f}ms, "
                    f"regions={avg(perf_stats['regions']):.1f}, "
                    f"detections={avg(perf_stats['detections']):.1f}, "
                    f"tracked={avg(perf_stats['tracked']):.1f}"
                )

        except KeyboardInterrupt:
            proc_logger.info("收到键盘中断,退出")
            break
        except Exception as e:
            proc_logger.error(f"门控管道异常: {e}", exc_info=True)
            time.sleep(0.1)

    proc_logger.info(f"门控检测管道已退出 (共处理 {frame_count} 帧, 推理 {infer_count} 帧)")