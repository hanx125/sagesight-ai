"""
事件语义去重服务（大模型多图对比）

属于大模型模块，用于事件去重的语义层：让大模型同时看当前事件快照和
近期事件快照，判断是否实质重复（同一目标、同一位置、同一行为）。

规则层去重（时间窗口 + 同类别 + bbox IoU，确定性规则、不消耗 token）
已独立为 src/events/rule_dedup.py，属于事件管理功能，不受大模型开关影响。
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
    """事件语义去重服务（大模型多图对比，属于大模型模块）"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = LLMClient(config)
        self.task_manager = TaskManager()

    def semantic_check(
        self,
        event_id: str,
        camera_id: str,
        class_name: str,
        image_bytes: bytes,
    ) -> Tuple[str, str]:
        """语义层去重：大模型对比当前快照与近期事件快照

        仅对规则层未判定重复的事件调用（规则层由事件管理独立执行）。

        Args:
            event_id: 当前事件 ID
            camera_id: 摄像机 ID
            class_name: 目标类别
            image_bytes: 当前事件快照（JPEG）

        Returns:
            (dedup_status, dedup_reason)
            dedup_status: "normal"（正常）/ "duplicate"（重复）
            dedup_reason: 判定原因
        """
        # 全局总开关：关闭时不调用大模型（运行时立即生效，防止 token 费用）
        from src.system.system_manager import get_system_manager
        if not get_system_manager().is_llm_enabled():
            logger.debug("[EventDedup] 大模型功能总开关已关闭，跳过语义去重")
            return "normal", ""

        if not self.config.enable_dedup or image_bytes is None:
            return "normal", ""

        # 模型不支持图片输入时无法进行图像对比，直接放行
        if not self.config.support_images:
            logger.debug("[EventDedup] 模型不支持图片输入(support_images=false)，跳过语义去重")
            return "normal", ""

        # 查询近期事件（窗口复用事件规则去重配置）
        from src.system.system_config_loader import get_event_dedup_config
        window = int(get_event_dedup_config().get('window_seconds', 300))
        try:
            recent_events = self.task_manager.get_recent_events(
                camera_id=camera_id,
                class_name=class_name,
                seconds=window,
                exclude_event_id=event_id,
                limit=20,
            )
        except Exception as e:
            logger.warning(f"[EventDedup] 查询近期事件失败，放行: {e}")
            return "normal", ""

        # 无近期事件可对比，放行
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
