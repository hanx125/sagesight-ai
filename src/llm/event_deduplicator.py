"""
事件去重服务

两层去重策略：
1. 规则层（快速，毫秒级）：时间窗口 + 同相机 + 同类别 + bbox IoU 重叠
   过滤 90% 的明显重复事件，不调用大模型
2. 语义层（大模型，秒级）：仅对规则层未过滤的候选事件，
   让大模型对比当前事件快照与近期事件快照，判断是否语义重复

规则层判定重复的条件（全部满足）：
- 同一 camera_id
- 同一 class_name
- 时间窗口内（默认 5 分钟）
- bbox IoU > 阈值（默认 0.3）

语义层通过多图多模态调用，让大模型同时看当前事件和近期事件的快照，
判断是否为同一事件的重复上报。
"""

import logging
from typing import List, Optional, Tuple

from src.llm.llm_client import LLMClient
from src.system.system_manager import LLMConfig
from src.tasks.task_manager import TaskManager, EventRecord

logger = logging.getLogger(__name__)

# 语义去重系统提示词
_DEDUP_SYSTEM_PROMPT = (
    "你是一个智能视频监控去重助手。我会给你几张监控快照，"
    "其中第一张是当前事件，其余是近期已记录的事件。"
    "请判断当前事件是否与某个近期事件实质重复"
    "（同一目标、同一位置、同一行为的重复触发）。"
    "只回复 JSON：{\"duplicate\": true/false, \"reason\": \"简要原因\"}"
)


class EventDeduplicator:
    """事件去重服务：规则层 + 语义层"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = LLMClient(config)
        self.task_manager = TaskManager()

    def check_duplicate(
        self,
        event_id: str,
        camera_id: str,
        class_name: str,
        bbox: List[float],
        image_bytes: Optional[bytes] = None,
    ) -> Tuple[str, str]:
        """检查事件是否重复

        Args:
            event_id: 当前事件 ID
            camera_id: 摄像机 ID
            class_name: 目标类别
            bbox: 检测框 [x1, y1, x2, y2]
            image_bytes: 当前事件快照（JPEG），用于语义层对比

        Returns:
            (dedup_status, dedup_reason)
            dedup_status: "normal"（正常）/ "duplicate"（重复）
            dedup_reason: 判定原因
        """
        window = self.config.dedup_window_seconds
        iou_threshold = self.config.dedup_iou_threshold

        # 第一层：规则层快速去重
        recent_events = self.task_manager.get_recent_events(
            camera_id=camera_id,
            class_name=class_name,
            seconds=window,
            exclude_event_id=event_id,
            limit=20,
        )

        for prev in recent_events:
            iou = _compute_iou(bbox, prev.bbox)
            if iou > iou_threshold:
                reason = f"规则去重：与事件 {prev.event_id[:8]} IoU={iou:.2f}（{window}秒内同类别同位置）"
                logger.info(f"[EventDedup] {reason}")
                return "duplicate", reason

        # 规则层未命中，若未启用语义去重或无图片，直接放行
        if not self.config.enable_dedup or image_bytes is None:
            return "normal", ""

        # 第二层：语义层去重（仅对有快照且近期有事件的情况）
        if not recent_events:
            return "normal", ""

        return self._semantic_dedup(image_bytes, recent_events)

    def _semantic_dedup(
        self,
        current_image: bytes,
        recent_events: List[EventRecord],
    ) -> Tuple[str, str]:
        """语义层去重：大模型对比快照判断是否重复

        Args:
            current_image: 当前事件快照
            recent_events: 近期事件列表

        Returns:
            (dedup_status, dedup_reason)
        """
        # 收集近期事件的快照（最多取 3 张做对比，控制 token 消耗）
        images = [current_image]
        compared_events = []
        for ev in recent_events[:3]:
            img = _load_event_image(ev)
            if img is not None:
                images.append(img)
                compared_events.append(ev)

        # 没有可对比的近期快照，放行
        if len(images) < 2:
            return "normal", ""

        prompt = (
            f"第一张图是当前事件。后面 {len(images) - 1} 张是近期 "
            f"{len(compared_events)} 个已记录事件的快照。"
            f"请判断当前事件是否与其中某个近期事件实质重复。"
        )

        try:
            reply = self.client.chat_with_images(
                images=images,
                prompt=prompt,
                system_prompt=_DEDUP_SYSTEM_PROMPT,
            )
            return self._parse_dedup_reply(reply, compared_events)
        except Exception as e:
            logger.warning(f"[EventDedup] 语义去重大模型调用失败，放行: {e}")
            return "normal", f"语义去重失败：{e}"

    def _parse_dedup_reply(
        self, reply: str, compared_events: List[EventRecord]
    ) -> Tuple[str, str]:
        """解析大模型去重回复"""
        import json

        # 尝试提取 JSON
        reply = reply.strip()
        # 去除可能的 markdown 代码块标记
        if reply.startswith("```"):
            reply = reply.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            result = json.loads(reply)
            is_dup = result.get("duplicate", False)
            reason = result.get("reason", "")
            if is_dup:
                # 尝试找到匹配的事件
                matched = compared_events[0] if compared_events else None
                event_ref = f"事件 {matched.event_id[:8]}" if matched else "近期事件"
                full_reason = f"语义去重：与{event_ref}重复（{reason}）"
                logger.info(f"[EventDedup] {full_reason}")
                return "duplicate", full_reason
            return "normal", f"语义去重：非重复（{reason}）"
        except (json.JSONDecodeError, KeyError):
            # JSON 解析失败，保守放行
            logger.debug(f"[EventDedup] 去重回复解析失败，放行: {reply[:100]}")
            return "normal", "语义去重回复解析失败"


# ========================================
# 工具函数
# ========================================

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


def _load_event_image(event: EventRecord) -> Optional[bytes]:
    """从事件记录加载快照图片

    优先从本地图片 URL 读取，失败返回 None
    """
    import os
    import urllib.parse

    if not event.local_image_url:
        return None

    try:
        # 从 URL 提取本地文件路径
        # URL 格式: http://{host}:{port}/output/images/{event_id}.jpg
        parsed = urllib.parse.urlparse(event.local_image_url)
        path = parsed.path.lstrip('/')

        # 项目根目录
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))
        file_path = os.path.join(project_root, path)

        if os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                return f.read()
    except Exception as e:
        logger.debug(f"[EventDedup] 加载事件图片失败 {event.event_id[:8]}: {e}")

    return None
