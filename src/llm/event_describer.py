"""
事件描述服务

在事件结束时，将事件最佳快照图 + 检测元数据发送给多模态大模型，
生成自然语言描述存入事件记录。

调用时机：事件真正结束（_finalize_event_end）后异步执行
调用方式：通过线程池异步提交，不阻塞事件管道
"""

import logging
import time
from datetime import datetime
from typing import Optional

from src.llm.llm_client import LLMClient
from src.system.system_manager import LLMConfig

logger = logging.getLogger(__name__)

# 事件描述系统提示词
_SYSTEM_PROMPT = (
    "你是一个智能视频监控助手。根据提供的监控快照和检测信息，"
    "生成规范统一的事件描述。必须严格按照以下四行模板输出，"
    "每行以【字段】开头，不要添加任何额外解释或技术细节：\n"
    "【目标】事件中的目标类别\n"
    "【行为】一句话客观描述目标的行为过程\n"
    "【位置】所在监控点位和区域\n"
    "【时间】事件发生时间及持续时间\n"
    "示例：\n"
    "【目标】行人\n"
    "【行为】一名行人从画面右侧快速横穿警戒区\n"
    "【位置】东门入口摄像头 · 警戒区\n"
    "【时间】10:23:45 起，持续约 5 秒\n"
    "不要描述检测框、置信度等技术细节。"
)


class EventDescriber:
    """事件描述服务：用多模态大模型生成事件自然语言描述"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = LLMClient(config)

    def describe(self, image_bytes: bytes, event_info: dict) -> Optional[str]:
        """生成事件描述

        Args:
            image_bytes: 事件最佳快照 JPEG 字节数据
            event_info: 事件元数据，包含:
                - class_name: 目标类别
                - confidence: 置信度
                - camera_name: 摄像机名称
                - zone_name: 区域名称（可选）
                - start_time: 事件开始时间
                - end_time: 事件结束时间
                - duration: 持续时间（秒）
                - event_type: 事件类型（tracking/zone/loitering）

        Returns:
            事件描述文本，失败返回 None
        """
        # 全局总开关：关闭时不调用大模型（运行时立即生效，防止 token 费用）
        from src.system.system_manager import get_system_manager
        if not get_system_manager().is_llm_enabled():
            logger.debug("[EventDescriber] 大模型功能总开关已关闭，跳过描述生成")
            return None

        prompt = self._build_prompt(event_info)

        try:
            description = self.client.chat_with_image(
                image_bytes=image_bytes,
                prompt=prompt,
                system_prompt=_SYSTEM_PROMPT
            )
            description = description.strip()
            # 清理模型可能包裹的 markdown 代码块
            if description.startswith('```'):
                description = description.strip('`').strip()
                description = description.lstrip('python').strip()
            if description:
                logger.info(
                    f"[EventDescriber] 事件描述生成成功: "
                    f"class={event_info.get('class_name')}, "
                    f"desc={description[:80]}"
                )
            return description if description else None
        except Exception as e:
            logger.warning(f"[EventDescriber] 事件描述生成失败: {e}")
            return None

    def _build_prompt(self, info: dict) -> str:
        """构建事件描述提示词"""
        class_name = info.get('class_name', '未知目标')
        camera_name = info.get('camera_name', '监控摄像头')
        zone_name = info.get('zone_name', '')
        event_type = info.get('event_type', 'tracking')
        duration = info.get('duration', 0)
        start_time = info.get('start_time', '')

        # 事件类型转中文描述
        type_desc = {
            'tracking': '目标检测',
            'zone': '区域入侵',
            'loitering': '徘徊停留'
        }.get(event_type, '目标检测')

        parts = [
            f"这是一张监控快照。检测信息如下：",
            f"- 检测目标：{class_name}",
            f"- 摄像头：{camera_name}",
        ]
        if zone_name:
            parts.append(f"- 所在区域：{zone_name}")
        parts.append(f"- 事件类型：{type_desc}")
        if duration and duration > 0:
            parts.append(f"- 持续时间：{duration:.1f}秒")
        if start_time:
            parts.append(f"- 开始时间：{start_time}")

        parts.append("请根据图片和以上信息，严格按照系统规定的四行模板输出事件描述。")
        return "\n".join(parts)
