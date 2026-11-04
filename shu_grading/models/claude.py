"""Anthropic Claude provider."""

from __future__ import annotations

import logging
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import ChatMessage, ChatResponse, ModelProvider

logger = logging.getLogger(__name__)


class ClaudeProvider(ModelProvider):
    """Anthropic Claude provider.

    支持 vision,使用 Anthropic Messages API。
    """

    name = "claude"

    def __init__(
        self,
        model_name: str = "claude-sonnet-4-5",
        api_key: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.1,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name, **kwargs)
        if not api_key:
            raise ValueError(
                "ClaudeProvider 需要 ANTHROPIC_API_KEY."
                " 请在 .env 中设置或传入 api_key 参数。"
            )
        # 延迟导入,避免无 anthropic 包时启动失败
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=api_key)
        self.max_tokens = max_tokens
        self.temperature = temperature


    def validate_config(self) -> list[str]:
        return [] if self.client else ["Claude client 未初始化"]


    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        reraise=True,
    )
    async def chat(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        # 分离 system message 和 user/assistant messages
        system_text = ""
        chat_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                system_text = msg.content
                continue

            content: list[dict[str, Any]] = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})

            for img in msg.images:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.media_type,
                            "data": img.base64_data,
                        },
                    }
                )

            chat_messages.append({"role": msg.role, "content": content or ""})

        if not chat_messages:
            raise ValueError("至少需要一条 user/assistant 消息")

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": (
                temperature if temperature is not None else self.temperature
            ),
            "messages": chat_messages,
        }
        if system_text:
            kwargs["system"] = system_text

        # Anthropic 没有原生 JSON mode,我们在 system prompt 中强化约束
        # 这里依赖 prompts/*.txt 已经写好了严格 JSON 输出要求
        response = await self.client.messages.create(**kwargs)

        # 提取文本
        text_parts = [
            block.text
            for block in response.content
            if hasattr(block, "text")
        ]
        content_str = "".join(text_parts)

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        return ChatResponse(
            content=content_str, usage=usage, model=self.model_name, raw=response
        )
