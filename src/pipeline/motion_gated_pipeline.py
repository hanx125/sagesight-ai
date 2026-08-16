"""
运动门控检测流水线（motion_gated）

在共享门控引擎（gated_pipeline.run_gated_pipeline）基础上，以 mode='motion_gated' 运行：
仅当本帧有运动（motion_boxes 非空）时推理；无运动时跳过推理。为防止长时间静止画面
漏检停在原地/缓慢移动的目标，每 motion_gate_fallback_interval 帧强制补抽一次。
解码、运动检测、跟踪、输出持续执行。
"""

from typing import Optional

from src.pipeline.gated_pipeline import run_gated_pipeline


def run_motion_gated_pipeline(
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
    motion_gate_fallback_interval: int = 30,
):
    """运动门控检测主循环

    有运动才推理，无运动跳过；每 motion_gate_fallback_interval 帧兜底补抽一次。
    委托共享门控引擎，mode='motion_gated'。
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
        mode='motion_gated',
        motion_gate_fallback_interval=motion_gate_fallback_interval,
    )