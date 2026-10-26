"""
大模型故障转移路由（多模型自动切换，对上层服务无感）

核心能力：
1. 自动检测：每次真实调用即检测，配置保存后还会后台探测一次并推送结果
2. 快速切换：某个模型调用失败时立即熔断，马上切换下一个候选模型
3. 任务重试：切换后任务用新模型从头重新执行（LLM 调用无状态，天然可重试）
4. 无感切换：对外接口与 LLMClient 完全一致，上层（事件描述/语义去重）零改动
5. 候选范围：所有「已启用」的配置，默认配置优先，其余按添加顺序；
   每次调用都实时读取配置，页面增删改立即生效

熔断策略：
- 失败后冷却 5 分钟（期间不再选中，直接跳过）
- 连续失败按指数退避：5min -> 10min -> 20min -> 30min（上限）
- 冷却到期后自动重新参与候选，下一次调用即重新探测（自愈）
- 探测/调用成功即清除失败状态

密钥解析：
- 优先使用配置自身密钥
- 仅 OpenRouter 的**免费模型**（模型 ID 以 :free 结尾，或官方免费路由
  openrouter/free）可回退全局密钥（system_settings.openrouter_api_key），
  实现「一处配置密钥，所有免费模型共用」；付费模型必须单独配置密钥
"""

import logging
import threading
import time
from dataclasses import replace
from typing import List, Optional, Tuple

from src.llm.llm_client import LLMClient
from src.system.system_manager import LLMConfig, get_system_manager

logger = logging.getLogger(__name__)

# 熔断冷却时间（秒）
_BASE_COOLDOWN = 300      # 首次失败冷却 5 分钟
_MAX_COOLDOWN = 1800      # 连续失败冷却上限 30 分钟


class LLMRouter:
    """大模型故障转移路由器（进程内单例）"""

    def __init__(self):
        self._lock = threading.RLock()
        self._fail_count = {}       # config_id -> 连续失败次数
        self._cooldown_until = {}   # config_id -> 冷却截止时间戳
        # 各线程最近一次成功调用的模型信息（线程隔离：事件描述与语义去重
        # 在各自线程并发执行，不能互相覆盖，否则写库会记错模型）
        self._local = threading.local()

    # ========================================
    # 最近成功模型记录（供上层标注描述由哪个模型生成）
    # ========================================

    def _record_used(self, config: LLMConfig) -> None:
        self._local.last_used = {
            'id': config.id,
            'name': config.name,
            'model': config.model,
        }

    def get_last_used(self) -> Optional[dict]:
        """获取当前线程最近一次成功调用使用的模型信息（无则 None）"""
        return getattr(self._local, 'last_used', None)

    def get_last_used_model(self, fallback: str = '自动切换') -> str:
        """获取当前线程最近一次成功调用使用的模型 ID（无则返回 fallback）"""
        info = self.get_last_used()
        return info['model'] if info and info.get('model') else fallback

    # ========================================
    # 候选模型与密钥解析
    # ========================================

    @staticmethod
    def is_openrouter_free_model(model: str) -> bool:
        """判断是否 OpenRouter 免费模型

        免费模型 ID 以 :free 结尾，或为官方免费路由 openrouter/free。
        全局密钥仅对此类模型生效，付费模型必须单独配置密钥。
        """
        m = (model or '').strip().lower()
        return m.endswith(':free') or m == 'openrouter/free'

    def _resolve_config(self, config: LLMConfig) -> Optional[LLMConfig]:
        """解析有效密钥，缺少密钥返回 None

        仅 OpenRouter 免费模型可回退全局密钥，付费模型需自身配置密钥。
        """
        api_key = config.api_key
        if (not api_key
                and config.provider.lower() == 'openrouter'
                and self.is_openrouter_free_model(config.model)):
            api_key = get_system_manager().get_setting('openrouter_api_key', '')
        if not api_key:
            return None
        return replace(config, api_key=api_key)

    def get_candidates(self, feature: str = None,
                       require_images: bool = False) -> List[LLMConfig]:
        """获取当前可用候选模型（实时读取配置，页面改动立即生效）

        Args:
            feature: 功能开关过滤（如 'enable_description' / 'enable_dedup'），
                     只保留开关打开的配置
            require_images: True 时只保留支持图片输入的配置

        Returns:
            有效配置列表（默认配置优先，其余按添加顺序；密钥已解析）
        """
        try:
            configs = get_system_manager().get_all_llm_configs()
        except Exception as e:
            logger.error(f"[LLMRouter] 读取大模型配置失败: {e}")
            return []

        now = time.time()
        candidates: List[LLMConfig] = []
        with self._lock:
            for c in configs:
                if not c.enabled:
                    continue
                if feature and not getattr(c, feature, True):
                    continue
                if require_images and not c.support_images:
                    continue
                # 熔断冷却期内跳过
                if now < self._cooldown_until.get(c.id, 0):
                    continue
                resolved = self._resolve_config(c)
                if resolved is None:
                    continue
                candidates.append(resolved)

        # 默认配置优先，其余按添加顺序，保证切换顺序稳定可预期
        candidates.sort(key=lambda c: (0 if c.is_default else 1, c.id))
        return candidates

    def has_candidates(self, feature: str = None) -> bool:
        """是否存在可用候选模型"""
        return bool(self.get_candidates(feature=feature))

    def get_failover_order(self) -> List[dict]:
        """获取切换顺序（用于日志/展示）"""
        return [
            {'id': c.id, 'name': c.name, 'model': c.model,
             'provider': c.provider, 'is_default': c.is_default}
            for c in self.get_candidates()
        ]

    # ========================================
    # 熔断状态管理
    # ========================================

    def mark_failed(self, config_id: int, reason: str = '') -> int:
        """标记调用失败：进入冷却（指数退避），返回本次冷却秒数"""
        with self._lock:
            n = self._fail_count.get(config_id, 0) + 1
            self._fail_count[config_id] = n
            cooldown = min(_BASE_COOLDOWN * (2 ** (n - 1)), _MAX_COOLDOWN)
            self._cooldown_until[config_id] = time.time() + cooldown
        logger.warning(
            f"[LLMRouter] 模型 #{config_id} 不可用，熔断 {cooldown}s: {reason}"
        )
        return cooldown

    def mark_success(self, config_id: int) -> None:
        """标记调用成功：清除失败状态"""
        with self._lock:
            had_failure = config_id in self._fail_count
            self._fail_count.pop(config_id, None)
            self._cooldown_until.pop(config_id, None)
        if had_failure:
            logger.info(f"[LLMRouter] 模型 #{config_id} 恢复可用")

    # ========================================
    # 统一执行入口（失败自动切换，任务重新执行）
    # ========================================

    def _execute(self, method_name: str, feature: Optional[str],
                 require_images: bool, *args, **kwargs) -> str:
        """按候选顺序执行，失败立即切换下一个；全部失败抛异常

        每次尝试都是完整重新执行任务（LLM 调用无状态），对上层无感。
        """
        candidates = self.get_candidates(feature=feature,
                                         require_images=require_images)
        # 图片任务没有视觉候选时，回退全部候选（LLMClient 会自动降级纯文本）
        if not candidates and require_images:
            candidates = self.get_candidates(feature=feature)

        if not candidates:
            raise RuntimeError(
                '没有可用的大模型配置（未配置/未启用/缺少 API 密钥）'
            )

        errors = []
        for c in candidates:
            try:
                result = getattr(LLMClient(c), method_name)(*args, **kwargs)
                self.mark_success(c.id)
                self._record_used(c)
                return result
            except Exception as e:
                errors.append(f"{c.name}(#{c.id}): {e}")
                self.mark_failed(c.id, str(e))
                logger.warning(
                    f"[LLMRouter] 模型「{c.name}」调用失败，"
                    f"自动切换下一个候选（候选共 {len(candidates)} 个）"
                )

        raise RuntimeError('所有大模型均调用失败 | ' + ' | '.join(errors))

    def chat_text(self, prompt: str, system_prompt: str = '',
                  feature: str = None) -> str:
        """纯文本对话（失败自动切换）"""
        return self._execute('chat_text', feature, False,
                             prompt, system_prompt)

    def chat_with_image(self, image_bytes: bytes, prompt: str,
                        system_prompt: str = '',
                        feature: str = None) -> str:
        """单图多模态对话（失败自动切换）"""
        return self._execute('chat_with_image', feature, True,
                             image_bytes, prompt, system_prompt)

    def chat_with_images(self, images: List[bytes], prompt: str,
                         system_prompt: str = '',
                         feature: str = None) -> str:
        """多图多模态对话（失败自动切换）"""
        return self._execute('chat_with_images', feature, True,
                             images, prompt, system_prompt)

    # ========================================
    # 连通性探测（自动检测）
    # ========================================

    def probe(self, config: LLMConfig) -> Tuple[bool, str]:
        """探测模型连通性（发送极小文本请求，消耗可忽略）

        探测结果同步写入熔断状态：
        - 失败 -> 立即熔断，真实调用会自动跳过并切换下一个
        - 成功 -> 清除失败状态

        Returns:
            (是否可用, 回复摘要或错误信息)
        """
        resolved = self._resolve_config(config)
        if resolved is None:
            return False, ('未配置 API 密钥（请在配置中单独设置；'
                           'OpenRouter 免费模型也可使用全局密钥）')
        try:
            reply = LLMClient(resolved).chat_text("请回复'连接成功'四个字。")
            self.mark_success(config.id)
            text = (reply or '').strip()
            return True, text[:50] if text else '连接成功'
        except Exception as e:
            self.mark_failed(config.id, str(e))
            return False, str(e)[:200]


# ========================================
# 进程内单例
# ========================================

_router: Optional[LLMRouter] = None
_router_lock = threading.Lock()


def get_llm_router() -> LLMRouter:
    """获取路由器单例"""
    global _router
    with _router_lock:
        if _router is None:
            _router = LLMRouter()
        return _router
