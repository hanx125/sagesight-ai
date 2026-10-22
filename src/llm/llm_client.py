"""
大模型客户端封装

统一支持 OpenAI 和 Anthropic 两种 API 格式的多模态调用。
两种格式均通过 HTTP REST API 调用，不依赖官方 SDK，减少依赖。

OpenAI 格式（兼容 OpenAI / 智谱 GLM / 通义 / DeepSeek 等）:
    POST {base_url}/chat/completions
    headers: Authorization: Bearer {api_key}
    body: { model, messages, temperature, max_tokens }
    message 含 image_url 时用 base64 data URI

Anthropic 格式:
    POST {base_url}/messages
    headers: x-api-key: {api_key}, anthropic-version: 2023-06-01
    body: { model, messages, max_tokens, temperature }
    message 含 image 时用 base64 source block
"""

import base64
import logging
import time
from typing import List, Optional

import requests

from src.system.system_manager import LLMConfig

logger = logging.getLogger(__name__)

# 请求超时（秒）：连接 10s，读取 120s（大模型响应可能较慢）
_REQUEST_TIMEOUT = (10, 120)


class LLMClient:
    """大模型客户端，统一封装 OpenAI / Anthropic 两种 API 格式"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.provider = config.provider.lower()

    # ========================================
    # 纯文本对话
    # ========================================

    def chat_text(self, prompt: str, system_prompt: str = "") -> str:
        """纯文本对话

        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词（可选）

        Returns:
            模型回复文本
        """
        if self.provider == 'anthropic':
            return self._chat_anthropic_text(prompt, system_prompt)
        return self._chat_openai_text(prompt, system_prompt)

    # ========================================
    # 多模态对话（图片 + 文本）
    # ========================================

    def chat_with_image(self, image_bytes: bytes, prompt: str,
                        system_prompt: str = "") -> str:
        """单图多模态对话

        当配置的模型不支持图片输入（support_images=false）时，
        自动降级为纯文本请求（不带图片），避免部分纯文本模型返回 400。

        Args:
            image_bytes: JPEG/PNG 图片字节数据
            prompt: 用户提示词
            system_prompt: 系统提示词（可选）

        Returns:
            模型回复文本
        """
        if not self.config.support_images:
            logger.info("[LLMClient] 模型不支持图片输入(support_images=false)，降级为纯文本请求")
            return self.chat_text(prompt, system_prompt)
        if self.provider == 'anthropic':
            return self._chat_anthropic_image(image_bytes, prompt, system_prompt)
        return self._chat_openai_image(image_bytes, prompt, system_prompt)

    def chat_with_images(self, images: List[bytes], prompt: str,
                         system_prompt: str = "") -> str:
        """多图多模态对话（用于语义去重对比）

        当配置的模型不支持图片输入（support_images=false）时，
        自动降级为纯文本请求（不带图片），避免部分纯文本模型返回 400。

        Args:
            images: 图片字节数据列表
            prompt: 用户提示词
            system_prompt: 系统提示词（可选）

        Returns:
            模型回复文本
        """
        if not self.config.support_images:
            logger.info("[LLMClient] 模型不支持图片输入(support_images=false)，降级为纯文本请求")
            return self.chat_text(prompt, system_prompt)
        if self.provider == 'anthropic':
            return self._chat_anthropic_images(images, prompt, system_prompt)
        return self._chat_openai_images(images, prompt, system_prompt)

    # ========================================
    # OpenAI 格式实现
    # ========================================

    def _openai_url(self) -> str:
        """构造 OpenAI API URL"""
        base = self.config.base_url.rstrip('/')
        if not base:
            # openrouter 走 OpenAI 兼容网关，其余走 OpenAI 官方地址
            base = ('https://openrouter.ai/api/v1'
                    if self.provider == 'openrouter'
                    else 'https://api.openai.com/v1')
        return f"{base}/chat/completions"

    def _chat_openai_text(self, prompt: str, system_prompt: str) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        resp = self._openai_request(messages)
        return resp

    def _chat_openai_image(self, image_bytes: bytes, prompt: str,
                           system_prompt: str) -> str:
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}"
                }}
            ]
        })

        return self._openai_request(messages)

    def _chat_openai_images(self, images: List[bytes], prompt: str,
                            system_prompt: str) -> str:
        content = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(img).decode('utf-8')
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        return self._openai_request(messages)

    def _openai_request(self, messages: list) -> str:
        """发送 OpenAI 格式请求，带重试逻辑（最多重试 1 次，共 2 次尝试）"""
        url = self._openai_url()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}"
        }
        if self.provider == 'openrouter':
            # OpenRouter 建议的应用标识头（可选，仅用于来源统计）
            headers["HTTP-Referer"] = "https://openrouter.ai/sagesight-ai"
            headers["X-Title"] = "SageSight AI"
        body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens
        }

        for attempt in range(2):
            start = time.time()
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=_REQUEST_TIMEOUT)
                elapsed = time.time() - start
                if resp.status_code != 200:
                    err = resp.text[:500]
                    logger.warning(f"[LLMClient] OpenAI 请求失败 {resp.status_code} (尝试 {attempt+1}/2, {elapsed:.1f}s): {err}")
                    if attempt == 0:
                        time.sleep(1)  # 重试前短暂等待
                        continue
                    raise RuntimeError(f"OpenAI API 返回 {resp.status_code}: {err}")

                data = resp.json()
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                # content 可能为 null（键存在但值为空，常见于思考模型，
                # 如 MiniMax M3.1 经 OpenRouter 返回 content=null），
                # 用 `or ""` 兜底；正文为空时回退 reasoning 字段
                text = msg.get("content") or ""
                if not text.strip():
                    text = msg.get("reasoning") or ""
                logger.debug(f"[LLMClient] OpenAI 响应 (尝试 {attempt+1}/2, {elapsed:.1f}s): {text[:100]}")
                return text
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f"[LLMClient] OpenAI 请求超时/连接错误 (尝试 {attempt+1}/2): {e}")
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise RuntimeError(f"OpenAI API 请求失败: {e}")
        raise RuntimeError("OpenAI API 多次尝试后仍失败")

    # ========================================
    # Anthropic 格式实现
    # ========================================

    def _anthropic_url(self) -> str:
        """构造 Anthropic API URL"""
        base = self.config.base_url.rstrip('/')
        if not base:
            base = 'https://api.anthropic.com/v1'
        return f"{base}/messages"

    def _chat_anthropic_text(self, prompt: str, system_prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        return self._anthropic_request(messages, system_prompt)

    def _chat_anthropic_image(self, image_bytes: bytes, prompt: str,
                              system_prompt: str) -> str:
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64
                }},
                {"type": "text", "text": prompt}
            ]
        }]
        return self._anthropic_request(messages, system_prompt)

    def _chat_anthropic_images(self, images: List[bytes], prompt: str,
                               system_prompt: str) -> str:
        content = []
        for img in images:
            b64 = base64.b64encode(img).decode('utf-8')
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64
                }
            })
        content.append({"type": "text", "text": prompt})

        messages = [{"role": "user", "content": content}]
        return self._anthropic_request(messages, system_prompt)

    def _anthropic_request(self, messages: list, system_prompt: str) -> str:
        """发送 Anthropic 格式请求，带重试逻辑（最多重试 1 次，共 2 次尝试）"""
        url = self._anthropic_url()
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01"
        }
        body = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature
        }
        if system_prompt:
            body["system"] = system_prompt

        for attempt in range(2):
            start = time.time()
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=_REQUEST_TIMEOUT)
                elapsed = time.time() - start
                if resp.status_code != 200:
                    err = resp.text[:500]
                    logger.warning(f"[LLMClient] Anthropic 请求失败 {resp.status_code} (尝试 {attempt+1}/2, {elapsed:.1f}s): {err}")
                    if attempt == 0:
                        time.sleep(1)  # 重试前短暂等待
                        continue
                    raise RuntimeError(f"Anthropic API 返回 {resp.status_code}: {err}")

                data = resp.json()
                # Anthropic 返回 content 是列表，取 text 类型的内容
                content_blocks = data.get("content", [])
                text = "".join(
                    block.get("text", "") for block in content_blocks
                    if block.get("type") == "text"
                )
                logger.debug(f"[LLMClient] Anthropic 响应 (尝试 {attempt+1}/2, {elapsed:.1f}s): {text[:100]}")
                return text
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f"[LLMClient] Anthropic 请求超时/连接错误 (尝试 {attempt+1}/2): {e}")
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise RuntimeError(f"Anthropic API 请求失败: {e}")
        raise RuntimeError("Anthropic API 多次尝试后仍失败")
