"""
OpenRouter 免费视觉模型预设与在线列表获取

OpenRouter (https://openrouter.ai) 提供统一网关访问各家模型，
免费模型 ID 以 `:free` 结尾，按 OpenAI 兼容格式调用：

    POST https://openrouter.ai/api/v1/chat/completions
    headers: Authorization: Bearer sk-or-...

本模块维护一份「免费且支持图片输入（视觉）」的精选内置列表，
并提供从 OpenRouter 官方接口实时拉取最新列表的能力（带缓存）。
仅返回视觉模型（input_modalities 含 image 且 output 含 text），
纯文本/嵌入/内容审核/音频生成等模型一律过滤。
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

# OpenRouter OpenAI 兼容网关地址
OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'

# 在线列表缓存有效期（秒）：6 小时
_CACHE_TTL = 6 * 3600

# 拉取在线列表的超时（连接 5s，读取 8s），避免拖慢页面
_FETCH_TIMEOUT = (5, 8)

# 模型 ID 中包含这些关键词的直接排除（非对话/非视觉用途）
_EXCLUDE_KEYWORDS = ('safety', 'moderation', 'embed', 'lyria', 'clip', 'rerank')

# 内置精选列表：免费 + 支持图片输入（2026-10 从 OpenRouter 官方接口筛选）
BUILTIN_FREE_VISION_MODELS: List[Dict[str, Any]] = [
    {
        'id': 'nvidia/nemotron-3.5-super-vl:free',
        'name': 'NVIDIA: Nemotron 3.5 Super VL (free)',
        'context_length': 1000000,
        'modality': 'text+image+video->text',
        'description': 'NVIDIA 开源多模态 MoE 模型（120B 总参/12B 激活），支持文本/图片/文档/截图输入，视觉理解能力强，适合事件快照描述与语义去重',
    },
    {
        'id': 'google/gemma-4-31b-it:free',
        'name': 'Google: Gemma 4 31B (free)',
        'context_length': 262144,
        'modality': 'text+image+video->text',
        'description': 'Google DeepMind 30.7B 稠密多模态模型，支持图片输入与 256K 上下文，通用能力均衡',
    },
    {
        'id': 'google/gemma-4-26b-a4b-it:free',
        'name': 'Google: Gemma 4 26B A4B (free)',
        'context_length': 262144,
        'modality': 'text+image+video->text',
        'description': 'Gemma 4 MoE 版本（25.2B 总参/3.8B 激活），推理速度快、资源占用低，适合高频调用场景',
    },
    {
        'id': 'minimax/minimax-m3.1-flash:free',
        'name': 'MiniMax: MiniMax M3.1 Flash (free)',
        'context_length': 1000000,
        'modality': 'text+image+video->text',
        'description': 'MiniMax 多模态 Flash 模型，100 万 token 超长上下文，多模态理解与指令遵循均衡',
    },
    {
        'id': 'dots-studio/dots-3-note-preview:free',
        'name': 'Dots Studio: Dots3-Note Preview (free)',
        'context_length': 512000,
        'modality': 'text+image->text',
        'description': 'Dots Studio 开源 MoE 模型（280B 总参/16B 激活），激活参数轻量，适合快照描述类任务',
    },
    {
        'id': 'thinkingmachines/inkling-small:free',
        'name': 'Thinking Machines: Inkling Small (free)',
        'context_length': 1048576,
        'modality': 'text+image+audio->text',
        'description': 'Thinking Machines Lab 开源多模态 MoE（276B 总参/12B 激活），通用推理与图像理解能力较强',
    },
    {
        'id': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
        'name': 'NVIDIA: Nemotron 3 Nano Omni (free)',
        'context_length': 256000,
        'modality': 'text+image+audio+video->text',
        'description': 'NVIDIA 30B-A3B 全模态模型，支持文本/图片/视频输入并带推理能力，适合复杂场景判断',
    },
    {
        'id': 'openrouter/free',
        'name': 'OpenRouter: Free Models Router',
        'context_length': 200000,
        'modality': 'text+image->text',
        'description': 'OpenRouter 官方免费路由，自动在当前可用的免费模型中随机选择（支持图片输入），稳定性一般，适合快速试水',
    },
]

# 在线列表缓存：{ts, source, updated_at, models}
_cache: Dict[str, Any] = {'ts': 0.0, 'source': None, 'updated_at': None, 'models': None}
_cache_lock = threading.Lock()


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _fetch_live_free_vision_models() -> List[Dict[str, Any]]:
    """从 OpenRouter 官方接口拉取模型列表，筛选免费 + 图片输入的对话模型"""
    resp = requests.get(f'{OPENROUTER_BASE_URL}/models', timeout=_FETCH_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get('data', [])

    models: List[Dict[str, Any]] = []
    for m in data:
        pricing = m.get('pricing') or {}
        try:
            if float(pricing.get('prompt', 1)) != 0 or float(pricing.get('completion', 1)) != 0:
                continue
        except (TypeError, ValueError):
            continue

        arch = m.get('architecture') or {}
        input_mods = arch.get('input_modalities') or []
        output_mods = arch.get('output_modalities') or []
        # 只要视觉模型：输入含 image、输出为 text
        if 'image' not in input_mods or 'text' not in output_mods:
            continue

        model_id = (m.get('id') or '').strip()
        if not model_id:
            continue
        low = model_id.lower()
        if any(k in low for k in _EXCLUDE_KEYWORDS):
            continue

        models.append({
            'id': model_id,
            'name': m.get('name') or model_id,
            'context_length': m.get('context_length'),
            'modality': arch.get('modality') or '',
            'description': ((m.get('description') or '').strip())[:200],
        })

    # 按模型名排序，保证列表稳定
    models.sort(key=lambda x: x['name'])
    return models


def _bg_refresh_cache() -> None:
    """后台线程刷新缓存（首次访问时预热，不阻塞请求）"""
    try:
        models = _fetch_live_free_vision_models()
        if models:
            with _cache_lock:
                _cache.update(models=models, ts=time.time(),
                              source='live', updated_at=_now_str())
            logger.info(f"[OpenRouter] 免费视觉模型列表已缓存 {len(models)} 个")
    except Exception as e:
        logger.debug(f"[OpenRouter] 后台刷新免费视觉模型列表失败: {e}")


def get_free_vision_models(force: bool = False) -> Dict[str, Any]:
    """获取免费视觉模型列表

    Args:
        force: True 时强制从 OpenRouter 在线拉取（用户点击刷新）

    Returns:
        {source: 'live'|'cache'|'builtin'|'builtin_refreshing',
         updated_at, models: [...], error(可选)}
    """
    now = time.time()

    # 非强制：命中缓存直接返回
    if not force:
        with _cache_lock:
            if _cache['models'] and (now - _cache['ts']) < _CACHE_TTL:
                return {
                    'source': _cache['source'],
                    'updated_at': _cache['updated_at'],
                    'models': _cache['models']
                }

    # 强制刷新（或缓存为空且明确要求）时同步拉取
    if force:
        try:
            models = _fetch_live_free_vision_models()
            if models:
                with _cache_lock:
                    _cache.update(models=models, ts=time.time(),
                                  source='live', updated_at=_now_str())
                return {
                    'source': 'live',
                    'updated_at': _cache['updated_at'],
                    'models': models
                }
            return {
                'source': 'builtin',
                'updated_at': None,
                'models': BUILTIN_FREE_VISION_MODELS,
                'error': 'OpenRouter 当前未返回可用的免费视觉模型'
            }
        except Exception as e:
            logger.warning(f"[OpenRouter] 拉取免费视觉模型列表失败: {e}")
            return {
                'source': 'builtin',
                'updated_at': None,
                'models': BUILTIN_FREE_VISION_MODELS,
                'error': f'在线获取失败（{e}），已显示内置预设列表'
            }

    # 首次访问：立即返回内置列表，同时后台线程预热在线缓存
    threading.Thread(target=_bg_refresh_cache, daemon=True).start()
    return {
        'source': 'builtin',
        'updated_at': None,
        'models': BUILTIN_FREE_VISION_MODELS
    }
