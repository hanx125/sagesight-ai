"""
摄像机检测管道主循环

核心流程（每帧执行）:
1. 解码: read_frame_i420 → (header, y_plane, u_plane, v_plane)
2. 运动检测(可选): motion_detector.detect(y_plane) → motion_boxes
3. Region 生成: motion_boxes + tracked_object_boxes → 聚类 → regions
4. 检测: 全帧检测（运动+跟踪都关闭）或逐 region 同步检测
5. 合并去重: reduce_detections → 去重后的检测列表
6. 跟踪(可选): NorfairTracker.match_and_update() → 更新跟踪状态
7. 输出: detected_objects_queue.put() → 事件处理进程

架构特点:
- 运动检测用于缩小检测区域（可选，开启时每帧执行）,而非触发检测
- 帧连续（不跳帧）,跟踪轨迹不断裂
- 同步检测（当帧完成）,非异步流水线
- 检测单元是 region（运动框+跟踪框聚类）或整帧（运动+跟踪都关闭时）
- 结果通过共享内存+Event传递,非队列返回RGB帧
- 事件处理在独立进程,非CameraWorker内
- 运动检测和跟踪均可通过任务配置独立关闭（None 表示关闭）
- 关闭运动检测+跟踪时自动切换为全帧检测模式
- 关闭跟踪时检测结果直接进入事件队列（无跟踪ID）
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

# 解码停滞提示：解码器持续无新帧时空转，仅低频打点提示（进程级重启策略已移除，
# 靠每日定时全系统重启兜底，不再超时退出自愈）
DECODER_STALL_LOG_INTERVAL = 30   # 停滞期间告警的间隔（秒）


def _malloc_trim():
    """调用 glibc malloc_trim(0) 强制归还空闲堆内存给操作系统

    CameraWorker 每帧分配/释放 YUV 平面、region 张量、检测结果等 numpy 数组，
    glibc arena 会保留空闲 chunk 导致 RSS 缓慢增长。
    malloc_trim(0) 让 glibc 将 free'd chunk 还给内核（munmap/sbrk）。
    仅 Linux 有效，其他平台静默跳过。
    """
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except Exception:
        pass


def run_pipeline(
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
):
    """
    CameraWorker 主循环

    每帧执行: 解码 → 运动检测(可选) → region生成 → 同步检测 → 跟踪(可选) → 输出

    Args:
        camera_id: 摄像机ID
        task_id: 任务ID（用于共享内存命名）
        decoder: 视频解码器（保留不变,提供 get_frame_index 和 ring_buffer）
        motion_detector: 运动检测器（ImprovedMotionDetector），None 表示关闭运动检测
        remote_detector: 远程检测器代理（RemoteObjectDetector）
        tracker: Norfair 跟踪器，None 表示关闭跟踪
        detected_objects_queue: 事件处理队列（输出8-元组）
        frame_shape: 帧形状 (height, width)
        model_size: 模型输入尺寸 (height, width)
        detect_threshold: 检测分数阈值
        stop_event: 停止事件
        ptz_metrics: PTZ 共享状态（可选）
        target_classes: 目标类别过滤列表（可选,只保留这些类别的检测）
        backend: 检测器后端（'onnx_cuda'/'onnx_cpu'/'rknn' 等）
            backend='rknn' 时启用 RGA 硬件预处理（RK3588 平台，失败自动回退 cv2）
    """
    proc_logger = logging.getLogger(f"pipeline.{camera_id}")
    proc_logger.info(
        f"检测管道启动: frame_shape={frame_shape}, "
        f"model_size={model_size}, threshold={detect_threshold}"
    )

    # backend=rknn 时启用 RGA 硬件预处理（RK3588 平台，失败自动回退 cv2）
    use_rga = (backend == 'rknn')
    if use_rga:
        proc_logger.info("RGA 硬件预处理已启用（backend=rknn）")

    # Linux（非 RK3588）启用 OpenCV CUDA 预处理（仅 resize 上 GPU，失败回退 cv2）
    # Windows 暂不做 GPU 预处理（推理已用 GPU，预处理 CPU 开销可接受）
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
    # shm 前缀由解码器对象提供：共享解码模式下为 sagesight___shared___{camera}_block，
    # 任务私有解码时与原 f-string 结果一致（事件进程按消息里的前缀回读帧）
    shm_name_prefix = decoder.shm_name

    # 性能统计
    frame_count = 0
    perf_stats = {
        "decode_ms": [],
        "motion_ms": [],
        "region_ms": [],
        "detect_ms": [],
        "track_ms": [],
        "total_ms": [],
        "regions": [],        # 每帧 region 数量
        "detections": [],     # 每帧检测结果数量
        "tracked": [],        # 每帧已跟踪对象数量
    }
    perf_log_interval = 100

    # 首帧标志
    first_frame = True

    # 解码停滞看门狗状态
    last_frame_time = time.time()      # 最近一次收到有效帧的时间
    last_stall_log_time = time.time()  # 停滞期间上次告警时间

    while not stop_event.is_set():
        try:
            cycle_start = time.time()

            # ========================================
            # 1. 解码（保留不变）
            # ========================================
            decode_start = time.time()
            frame_index = decoder.get_frame_index()

            if frame_index is None:
                # 解码器本次未产出新帧。原实现静默空转（零日志），导致解码器失效时
                # "检测停、进程活、无报错"。此处仅低频打点提示空转，不再超时退出
                # （进程级重启策略已移除，靠每日定时全系统重启兜底）。
                idle = time.time() - last_frame_time
                # 注意：循环轮询频率高于帧率，"本次无新帧"是常态，不能仅凭此告警。
                # 必须同时满足"确实已停滞 idle 秒"和"距上次告警满节流间隔"才输出，
                # 否则会把正常解码误报成"解码器 0s 无新帧"。
                if idle >= DECODER_STALL_LOG_INTERVAL and time.time() - last_stall_log_time >= DECODER_STALL_LOG_INTERVAL:
                    last_stall_log_time = time.time()
                    proc_logger.warning(
                        f"解码器 {idle:.0f}s 无新帧，检测管道空转等待..."
                    )
                time.sleep(0.05)
                continue

            header, y_plane, u_plane, v_plane = decoder.ring_buffer.read_frame_i420(
                frame_index.shm_block_id
            )

            if header is None or y_plane is None:
                proc_logger.warning("解码失败,跳过本帧")
                continue

            last_frame_time = time.time()
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
            # 2. 运动检测（可选，用于缩小检测区域）
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
            # 3. Region 生成（motion_boxes + tracked_object_boxes 聚类）
            # ========================================
            region_start = time.time()

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
            # 4. 同步检测
            # ========================================
            detect_start = time.time()

            all_detections = []

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
                # 有 regions（运动检测或跟踪提供）：逐 region 检测
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

            # ========================================
            # 5. 合并去重（多 region 检测结果 NMS）
            # ========================================
            if all_detections:
                reduced_detections = reduce_detections(frame_shape, all_detections)
            else:
                reduced_detections = []

            # ========================================
            # 6. 跟踪（更新跟踪状态，可选）
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
            # 计数指标也按帧追加，与耗时字段统一为"近100帧 avg"语义
            perf_stats["regions"].append(len(regions))
            perf_stats["detections"].append(len(reduced_detections))
            perf_stats["tracked"].append(len(tracked_objects_dict))

            if frame_count % perf_log_interval == 0:
                avg = lambda lst: sum(lst[-100:]) / max(1, len(lst[-100:]))
                proc_logger.debug(
                    f"性能统计(近100帧 avg): "
                    f"decode={avg(perf_stats['decode_ms']):.1f}ms, "
                    f"motion={avg(perf_stats['motion_ms']):.1f}ms, "
                    f"region={avg(perf_stats['region_ms']):.1f}ms, "
                    f"detect={avg(perf_stats['detect_ms']):.1f}ms, "
                    f"track={avg(perf_stats['track_ms']):.1f}ms, "
                    f"total={avg(perf_stats['total_ms']):.1f}ms, "
                    f"regions={avg(perf_stats['regions']):.1f}, "
                    f"detections={avg(perf_stats['detections']):.1f}, "
                    f"tracked={avg(perf_stats['tracked']):.1f}"
                )
                # 归还 glibc 堆碎片，防止 CameraWorker 进程 RSS 缓慢增长
                _malloc_trim()

        except KeyboardInterrupt:
            proc_logger.info("收到键盘中断,退出")
            break
        except Exception as e:
            proc_logger.error(f"管道异常: {e}", exc_info=True)
            time.sleep(0.1)

    proc_logger.info(f"检测管道已退出 (共处理 {frame_count} 帧)")
