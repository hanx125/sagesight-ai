"""
事件规则去重服务

独立于大模型模块的确定性去重（不调用大模型、不消耗 token）：
- 同一相机 + 同一类别 + 时间窗口内 + bbox IoU 超过阈值 → 判定重复

属于事件管理功能，配置来源为 system_config.yaml 的 event_dedup 段（单一真相源），
不受大模型总开关/大模型配置影响。
"""

import logging
from typing import List, Tuple

from src.tasks.task_manager import TaskManager

logger = logging.getLogger(__name__)


class RuleDeduper:
    """事件规则去重：时间窗口 + 同类别 + bbox IoU（确定性规则）"""

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.enabled = bool(cfg.get('enabled', True))
        self.window_seconds = int(cfg.get('window_seconds', 300))
        self.iou_threshold = float(cfg.get('iou_threshold', 0.3))
        self.task_manager = TaskManager()

    def check_duplicate(
        self,
        event_id: str,
        camera_id: str,
        class_name: str,
        bbox: List[float],
    ) -> Tuple[str, str]:
        """检查事件是否与近期事件重复（纯规则，不调用大模型）

        Args:
            event_id: 当前事件 ID
            camera_id: 摄像机 ID
            class_name: 目标类别
            bbox: 检测框 [x1, y1, x2, y2]

        Returns:
            (dedup_status, dedup_reason): "duplicate" 重复 / "normal" 正常
        """
        if not self.enabled:
            return "normal", ""

        try:
            recent_events = self.task_manager.get_recent_events(
                camera_id=camera_id,
                class_name=class_name,
                seconds=self.window_seconds,
                exclude_event_id=event_id,
                limit=20,
            )
        except Exception as e:
            logger.warning(f"[RuleDedup] 查询近期事件失败: {e}")
            return "normal", ""

        for prev in recent_events:
            iou = _compute_iou(bbox, prev.bbox)
            if iou > self.iou_threshold:
                reason = (
                    f"规则去重：与事件 {prev.event_id[:8]} "
                    f"IoU={iou:.2f}（{self.window_seconds}秒内同类别同位置）"
                )
                logger.info(f"[RuleDedup] {reason}")
                return "duplicate", reason

        return "normal", ""


def _compute_iou(bbox1: List[float], bbox2: List[float]) -> float:
    """计算两个 bbox 的 IoU

    Args:
        bbox1, bbox2: [x1, y1, x2, y2]

    Returns:
        IoU 值 [0, 1]，任一 bbox 无效返回 0
    """
    if not bbox1 or not bbox2 or len(bbox1) < 4 or len(bbox2) < 4:
        return 0.0

    x1 = max(bbox1[0], bbox2[0])
    y1 = max(bbox1[1], bbox2[1])
    x2 = min(bbox1[2], bbox2[2])
    y2 = min(bbox1[3], bbox2[3])

    inter_w = max(0, x2 - x1)
    inter_h = max(0, y2 - y1)
    inter_area = inter_w * inter_h

    area1 = max(0, bbox1[2] - bbox1[0]) * max(0, bbox1[3] - bbox1[1])
    area2 = max(0, bbox2[2] - bbox2[0]) * max(0, bbox2[3] - bbox2[1])
    union_area = area1 + area2 - inter_area

    if union_area <= 0:
        return 0.0

    return inter_area / union_area
