"""
大模型集成模块

提供多模态大模型对检测事件进行自然语言描述和语义去重的能力。

组件:
- LLMClient: 统一封装 OpenAI / Anthropic 两种 API 格式的多模态调用
- EventDescriber: 事件描述服务（快照图 + 检测元数据 → 自然语言描述）
- EventDeduplicator: 两层去重（规则层 IoU + 语义层大模型判断）
"""

from src.llm.llm_client import LLMClient
from src.llm.event_describer import EventDescriber
from src.llm.event_deduplicator import EventDeduplicator

__all__ = ['LLMClient', 'EventDescriber', 'EventDeduplicator']
