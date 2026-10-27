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
    "【目标】目标类别与数量，并补充画面中可见的显著外观特征（如颜色、衣着、车型、大小等）\n"
    "【行为】用一句话具体描述目标的行为过程：从画面哪个方位出现、朝哪个方向移动、"
    "以何种方式移动（快速/缓慢/静止/徘徊）以及正在做什么；"
    "只描述画面中确实可见的内容，信息不足时不要臆测\n"
    "【位置】只使用下方检测信息中给出的摄像头名称；提供了区域名称时补充区域名，"
    "未提供区域时只写摄像头名称，禁止编造地点\n"
    "【时间】事件开始时间（年-月-日 时:分:秒）及持续时间\n"
    "示例：\n"
    "【目标】1 名行人，身穿深色上衣、背双肩包\n"
    "【行为】该行人从画面右侧进入，快速横穿警戒区后向画面左上方离开\n"
    "【位置】东门入口摄像头；区域：警戒区\n"
    "【时间】2026-09-30 10:23:45 起，持续约 5 秒\n"
    "不要描述检测框、置信度等技术细节。"
)


def _format_time(value) -> str:
    """将事件开始时间统一格式化为可读字符串

    事件管道传入的是 epoch 秒（float），直接交给大模型会输出
    「1790745950.5585804」这类原始时间戳；此处统一转成
    「年-月-日 时:分:秒」，并兼容 datetime 与字符串。
    """
    if value is None or value == '':
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')
        except (OverflowError, OSError, ValueError):
            return str(value)
    return str(value)


class EventDescriber:
    """事件描述服务：用多模态大模型生成事件自然语言描述

    config 为 None 时走故障转移路由（多模型自动切换），否则固定单一模型。
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config
        # 告警限流：没有可用模型时每个事件都打一条 WARNING 会把日志刷爆，
        # 改成 5 分钟提示一次并累计被抑制的次数。
        self._last_unavailable_warn = 0.0
        self._suppressed_warn = 0
        if config is not None:
            self.client = LLMClient(config)
        else:
            from src.llm.llm_router import get_llm_router
            self.client = get_llm_router()

    @property
    def model_label(self) -> str:
        """用于写库标注的模型标识

        固定单模型模式下为配置的模型 ID；路由模式下 config 为 None，
        取本线程最近一次成功调用的实际模型（自动故障转移时可能已切换）。
        """
        if self.config is not None:
            return self.config.model or ''
        try:
            return self.client.get_last_used_model(fallback='自动切换')
        except Exception:
            return '自动切换'

    def describe(self, image_bytes: bytes, event_info: dict) -> Optional[str]:
        """生成事件描述

        Args:
            image_bytes: 事件最佳快照 JPEG 字节数据
            event_info: 事件元数据，包含:
                - class_name: 目标类别
                - confidence: 置信度
                - camera_name: 摄像机通道名称
                - zone_name: 区域名称（可选）
                - start_time: 事件开始时间（epoch 秒或时间字符串，输出前统一格式化）
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

        # 路由模式：先确认是否有可用候选，没有就限流提示并跳过（避免刷日志）
        if self.config is None and not self._has_candidates():
            self._warn_unavailable()
            return None

        try:
            if self.config is not None:
                description = self.client.chat_with_image(
                    image_bytes=image_bytes,
                    prompt=prompt,
                    system_prompt=_SYSTEM_PROMPT
                )
            else:
                # 路由模式：失败自动切换下一个模型并重新执行任务
                description = self.client.chat_with_image(
                    image_bytes, prompt, _SYSTEM_PROMPT,
                    feature='enable_description'
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

    def _has_candidates(self) -> bool:
        """当前是否存在可用（密钥齐备且未熔断）的事件描述候选模型"""
        try:
            return self.client.has_candidates(feature='enable_description')
        except Exception:
            return True  # 读取失败时交给真实调用去报错，避免误判

    def _warn_unavailable(self) -> None:
        """限流地提示「当前没有可用模型」，并给出可诊断原因"""
        now = time.time()
        if now - self._last_unavailable_warn < 300:
            self._suppressed_warn += 1
            logger.debug("[EventDescriber] 仍无可用大模型，跳过描述生成")
            return
        self._last_unavailable_warn = now
        try:
            reason = self.client.describe_unavailable(feature='enable_description')
        except Exception:
            reason = '没有可用的大模型配置'
        extra = f"（期间抑制了 {self._suppressed_warn} 条同类提示）" if self._suppressed_warn else ''
        self._suppressed_warn = 0
        logger.warning(f"[EventDescriber] 跳过事件描述：{reason}{extra}")

    def _build_prompt(self, info: dict) -> str:
        """构建事件描述提示词"""
        class_name = info.get('class_name', '未知目标')
        camera_name = info.get('camera_name') or '未知摄像头'
        zone_name = info.get('zone_name', '')
        event_type = info.get('event_type', 'tracking')
        duration = info.get('duration', 0)
        start_time = _format_time(info.get('start_time'))

        # 事件类型转中文描述
        type_desc = {
            'tracking': '目标检测',
            'zone': '区域入侵',
            'loitering': '徘徊停留'
        }.get(event_type, '目标检测')

        parts = [
            f"这是一张监控快照。检测信息如下：",
            f"- 检测目标：{class_name}",
            f"- 摄像头名称：{camera_name}",
            f"- 所在区域：{zone_name}" if zone_name else "- 所在区域：未设置",
            f"- 事件类型：{type_desc}",
        ]
        if duration and duration > 0:
            parts.append(f"- 持续时间：{duration:.1f}秒")
        if start_time:
            parts.append(f"- 事件开始时间：{start_time}")

        parts.append(
            "请根据图片和以上信息，严格按照系统规定的四行模板输出事件描述；"
            "【位置】行只能使用上面给出的摄像头名称与区域名称，未提供区域时不要编造地点名称。"
        )
        return "\n".join(parts)
