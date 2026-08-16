"""
流水线工厂

按任务 detection_type 选择对应的流水线函数，供 CameraWorker 调用。
- realtime     → camera_pipeline.run_pipeline（现有实时流水线，保持不变）
- polling      → polling_pipeline.run_polling_pipeline（轮询/周期检测）
- motion_gated → motion_gated_pipeline.run_motion_gated_pipeline（运动门控检测）
- file         → batch_pipeline.run_batch_pipeline（离线文件检测）
- scheduled    → batch_pipeline.run_batch_pipeline（定时批量检测，定时由外层 Scheduler 触发）
- 其他/未知    → 回退到实时流水线（向后兼容）
"""

import logging

logger = logging.getLogger(__name__)

VALID_DETECTION_TYPES = ('realtime', 'polling', 'motion_gated', 'file', 'scheduled')


def get_pipeline(detection_type: str):
    """根据检测类型返回流水线执行函数

    Args:
        detection_type: 检测类型（realtime / polling / motion_gated / file / scheduled）

    Returns:
        callable: 流水线执行函数，签名与 run_pipeline 一致的调用入口
    """
    detection_type = detection_type or 'realtime'

    if detection_type == 'polling':
        from src.pipeline.polling_pipeline import run_polling_pipeline
        return run_polling_pipeline

    if detection_type == 'motion_gated':
        from src.pipeline.motion_gated_pipeline import run_motion_gated_pipeline
        return run_motion_gated_pipeline

    if detection_type in ('file', 'scheduled'):
        from src.pipeline.batch_pipeline import run_batch_pipeline
        return run_batch_pipeline

    # realtime 及未知类型：回退到现有实时流水线
    if detection_type != 'realtime':
        logger.warning(
            f"未知检测类型 '{detection_type}'，回退到实时流水线"
        )
    from src.pipeline.camera_pipeline import run_pipeline
    return run_pipeline