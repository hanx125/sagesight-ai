"""
占道/堆积检测管道主循环

核心流程（每帧执行）:
1. 解码: read_frame_i420 → (header, y_plane, u_plane, v_plane)
2. 拍摄请求检查: 有拍摄标记 → 连取 5 帧 Y 平面取中值 → 落盘基线并复位状态机
3. 降频差分: 每 OCCUPANCY_INTERVAL_FRAMES 帧执行一次（6fps 解码下 ≈1s/次）
4. 状态回写: 每 STATUS_WRITE_INTERVAL 秒把区域状态摘要落盘，供 API 读取

架构特点:
- 不加载检测模型、不开运动检测、不开跟踪，纯 CPU 差分
- 状态机运行在本进程（持续持有解码帧）；事件生成经队列交给事件进程
- 迁移消息封装为 dict（__type__='occupancy'）混入 detected_objects_queue
- 基线按通道存储，任务重启后仍然有效
"""

import logging
import time
from datetime import datetime
from typing import Optional

from src.occupancy.baseline_manager import BASELINE_CAPTURE_FRAMES, BaselineManager
from src.occupancy.occupancy_state import ChannelOccupancyMonitor

logger = logging.getLogger(__name__)

# 差分执行间隔（帧数）：6fps 解码下每 6 帧 ≈ 1 秒一次
OCCUPANCY_INTERVAL_FRAMES = 6

# 基线拍摄时相邻两帧的间隔（秒），覆盖 2~3 秒窗口
CAPTURE_FRAME_INTERVAL = 0.5

# 状态回写间隔（秒）
STATUS_WRITE_INTERVAL = 5.0

# 解码停滞提示：解码器持续无新帧时空转，仅低频打点提示（与实时管道一致）
DECODER_STALL_LOG_INTERVAL = 30


def _malloc_trim():
    """调用 glibc malloc_trim(0) 强制归还空闲堆内存给操作系统（仅 Linux 有效）"""
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except Exception:
        pass


def _capture_baseline(decoder, proc_logger) -> Optional[tuple]:
    """连取多帧 Y 平面逐像素取中值，返回 (baseline_y, shm_block_id, sequence_num) 或 None"""
    y_planes = []
    last_meta = None
    for i in range(BASELINE_CAPTURE_FRAMES):
        if i > 0:
            time.sleep(CAPTURE_FRAME_INTERVAL)
        deadline = time.time() + 2.0
        got = None
        while time.time() < deadline:
            frame_index = decoder.get_frame_index()
            if frame_index is None:
                time.sleep(0.05)
                continue
            header, y_plane, _, _ = decoder.ring_buffer.read_frame_i420(
                frame_index.shm_block_id
            )
            if header is not None and y_plane is not None:
                got = (y_plane, frame_index.shm_block_id, header.sequence_num)
                break
            time.sleep(0.05)
        if got is None:
            proc_logger.warning(f"基线拍摄第 {i + 1}/{BASELINE_CAPTURE_FRAMES} 帧超时")
            return None
        y_planes.append(got[0])
        last_meta = got[1:]

    baseline_y = BaselineManager.capture_median(y_planes)
    return (baseline_y, last_meta[0], last_meta[1])


def run_occupancy_pipeline(
    camera_id: str,
    task_id: str,
    detection_type: str,
    decoder,
    detected_objects_queue,
    frame_shape: tuple,
    zones_config,
    stop_event,
    channel_id=None,
):
    """
    占道/堆积检测管道主循环

    Args:
        camera_id: 摄像机 ID
        task_id: 任务 ID
        detection_type: 'pile_up' 或 'abnormal_occupancy'
        decoder: 视频解码器
        detected_objects_queue: 事件处理队列（输出遗留物消息 dict）
        frame_shape: 帧形状 (height, width)
        zones_config: 通道级区域配置列表
        stop_event: 停止事件
        channel_id: 通道 ID（基线按通道存储；缺省用 camera_id）
    """
    proc_logger = logging.getLogger(f"pipeline.{camera_id}")
    baseline_channel = channel_id if channel_id else camera_id

    # 1. 构建通道监视器并加载已有基线（无基线 → UNCALIBRATED，等待拍摄）
    monitor = ChannelOccupancyMonitor(
        channel_id=baseline_channel,
        detection_type=detection_type,
        zones_config=zones_config,
        frame_shape=frame_shape,
    )
    if not monitor.zones:
        proc_logger.warning(
            f"无启用本检测类型的区域（detection_type={detection_type}），任务空转"
        )
    baseline_loaded = monitor.load_baseline()
    if baseline_loaded:
        proc_logger.info(
            f"占道检测管道启动: detection_type={detection_type}, "
            f"zones={len(monitor.zones)}, 基线已加载"
        )
    else:
        proc_logger.info(
            f"占道检测管道启动: detection_type={detection_type}, "
            f"zones={len(monitor.zones)}, 基线未校准，等待拍摄"
        )

    # shm 前缀由解码器对象提供：共享解码模式下为 sagesight___shared___{camera}_block，
    # 任务私有解码时与原 f-string 结果一致（事件进程按消息里的前缀回读帧）
    shm_name_prefix = decoder.shm_name

    frame_count = 0
    last_frame_time = time.time()      # 最近一次收到有效帧的时间
    last_stall_log_time = time.time()  # 停滞期间上次告警时间
    last_status_write = 0.0            # 上次状态回写时间
    first_frame = True

    while not stop_event.is_set():
        try:
            # ========================================
            # 1. 拍摄基线请求（置于解码之前：解码器无帧/流未就绪时
            #    也要消费请求并回写失败原因，否则前端永远停在"未校准"）
            # ========================================
            if BaselineManager.consume_capture_request(task_id, baseline_channel):
                proc_logger.info("收到基线拍摄请求，开始采集...")
                result = _capture_baseline(decoder, proc_logger)
                if result is not None:
                    baseline_y, block_id, seq_num = result
                    BaselineManager.save(
                        baseline_channel,
                        baseline_y,
                        {
                            'captured_at': datetime.now().isoformat(),
                            'frame_shape': [baseline_y.shape[0], baseline_y.shape[1]],
                            'task_id': str(task_id),
                            'camera_id': str(camera_id),
                        },
                    )
                    monitor.handle_baseline_captured(baseline_y, time.time())
                    proc_logger.info(
                        f"基线拍摄完成: shape={baseline_y.shape}, 已复位区域状态"
                    )
                else:
                    err_msg = '拍摄失败：解码器无视频帧（检查摄像机在线与转发流状态后重试）'
                    proc_logger.warning(f"基线拍摄失败: {err_msg}")
                    BaselineManager.save_status(baseline_channel, {
                        'state': 'capture_failed',
                        'error': err_msg,
                        'channel_id': baseline_channel,
                        'updated_at': time.time(),
                    })
                last_status_write = 0.0  # 立即回写新状态
                continue

            # ========================================
            # 2. 解码
            # ========================================
            frame_index = decoder.get_frame_index()
            if frame_index is None:
                idle = time.time() - last_frame_time
                if idle >= DECODER_STALL_LOG_INTERVAL and \
                        time.time() - last_stall_log_time >= DECODER_STALL_LOG_INTERVAL:
                    last_stall_log_time = time.time()
                    proc_logger.warning(
                        f"解码器 {idle:.0f}s 无新帧，占道检测管道空转等待..."
                    )
                time.sleep(0.05)
                continue

            header, y_plane, _, _ = decoder.ring_buffer.read_frame_i420(
                frame_index.shm_block_id
            )
            if header is None or y_plane is None:
                continue

            last_frame_time = time.time()
            frame_count += 1

            if first_frame:
                proc_logger.info(
                    f"首帧接收: seq={header.sequence_num}, shape={y_plane.shape}"
                )
                first_frame = False

            # ========================================
            # 4. 帧消息输出（每帧）：供事件进程推送 AI 视频流，
            #    帧本体在共享内存中，队列仅传递标识；无跟踪对象与检测结果
            # ========================================
            try:
                detected_objects_queue.put(
                    (
                        str(camera_id),
                        header.timestamp_ns / 1e9,
                        {},
                        [],
                        [],
                        frame_index.shm_block_id,
                        shm_name_prefix,
                        [],
                        header.sequence_num,
                    ),
                    timeout=0.05,
                )
            except Exception:
                pass  # AI 流帧消息允许丢弃（下一帧即续上），不告警避免刷屏

            # ========================================
            # 5. 降频差分（每 OCCUPANCY_INTERVAL_FRAMES 帧一次）
            # ========================================
            if frame_count % OCCUPANCY_INTERVAL_FRAMES != 0:
                _malloc_trim()
                continue

            now = time.time()
            messages = monitor.process_frame(y_plane, now)
            for msg in messages:
                msg.update({
                    '__type__': 'occupancy',
                    'camera_id': camera_id,
                    'detection_type': detection_type,
                    'task_id': str(task_id),
                    'shm_block_id': frame_index.shm_block_id,
                    'shm_name': shm_name_prefix,
                    'sequence_num': header.sequence_num,
                })
                try:
                    detected_objects_queue.put(msg, timeout=0.05)
                except Exception:
                    proc_logger.warning("事件队列满,丢弃遗留物消息")

            # ========================================
            # 6. 状态回写（定期，供 API/前端读取）
            # ========================================
            if now - last_status_write >= STATUS_WRITE_INTERVAL:
                last_status_write = now
                status = monitor.status_summary()
                meta = BaselineManager.meta(baseline_channel)
                if meta:
                    status['captured_at'] = meta.get('captured_at')
                status['channel_id'] = baseline_channel
                status['updated_at'] = now
                BaselineManager.save_status(baseline_channel, status)

            _malloc_trim()

        except Exception as e:
            proc_logger.error(f"占道检测管道异常: {e}")
            time.sleep(0.5)

    proc_logger.info("占道检测管道已退出")
