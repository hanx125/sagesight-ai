"""
轮询/周期检测流水线（polling）

在共享门控引擎（gated_pipeline.run_gated_pipeline）基础上，以 mode='polling' 运行：
每 polling_interval_frames 帧推理一次，其余帧跳过推理（保留跟踪状态维持轨迹）。
解码、运动检测、跟踪、输出持续执行，推理量降为 1/N。
"""

from typing import Optional

from src.pipeline.gated_pipeline import run_gated_pipeline


def run_polling_pipeline(
    camera_id: str,
    task_id: str,
    decoder,
    motion_detector: Optional[object],
    remote_detector,
    tracker: Optional[object],
    detected_objects_queue,
    frame_shape: tuple,
    model_size: tuple,
    detect_threshold: float,
    stop_event,
    ptz_metrics=None,
    target_classes: Optional[list] = None,
    backend: str = 'onnx_cuda',
    polling_interval_frames: int = 5,
):
    """轮询/周期检测主循环

    每 polling_interval_frames 帧推理一次，其余帧跳过推理。
    委托共享门控引擎，mode='polling'。
    """
    return run_gated_pipeline(
        camera_id=camera_id,
        task_id=task_id,
        decoder=decoder,
        motion_detector=motion_detector,
        remote_detector=remote_detector,
        tracker=tracker,
        detected_objects_queue=detected_objects_queue,
        frame_shape=frame_shape,
        model_size=model_size,
        detect_threshold=detect_threshold,
        stop_event=stop_event,
        ptz_metrics=ptz_metrics,
        target_classes=target_classes,
        backend=backend,
        mode='polling',
        polling_interval_frames=polling_interval_frames,
    )