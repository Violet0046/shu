"""OpenAI 兼容 provider.

支持 OpenAI、Azure OpenAI、DeepSeek、豆包（通过火山引擎方舟）等所有 OpenAI 兼容接口。
"""

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


class OpenAIProvider(ModelProvider):
    """OpenAI 兼容 provider（含豆包、DeepSeek 等）."""

    name = "openai"

    def __init__(
        self,
        model_name: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.1,
        provider_kind: str = "openai",  # 仅用于显示,不影响逻辑
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name, **kwargs)
        if not api_key:
            raise ValueError(
                "OpenAIProvider 需要 API key."
                " 请设置 OPENAI_API_KEY 或传入 api_key 参数。"
            )
        from openai import AsyncOpenAI

        # Avoid optional Brotli decoders incompatible with some SDK transports.
        self.client = AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            default_headers={"Accept-Encoding": "gzip, deflate"},
        )
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.provider_kind = provider_kind

    def supports_vision(self) -> bool:
        """OpenAI 视觉模型 + 豆包视觉模型."""
        vision_keywords = ("vision", "gpt-4o", "gpt-4-vision", "doubao", "vl")
        return any(kw in self.model_name.lower() for kw in vision_keywords)

    def validate_config(self) -> list[str]:
        errors: list[str] = []
        if not self.client:
            errors.append("OpenAI client 未初始化")
        return errors

    def estimate_cost(self, messages: list[ChatMessage]) -> float:
        """估算费用（元）。价格随 provider 不同,这里用近似值."""
        input_chars = sum(len(m.content) for m in messages)
        image_count = sum(len(m.images) for m in messages)
        input_tokens = input_chars * 0.5 + image_count * 800
        output_tokens = 800

        # 简化价格表(元/百万 token)
        if self.provider_kind == "doubao":
            input_price, output_price = 5, 9  # 豆包较便宜
        elif "gpt-4o" in self.model_name.lower():
            input_price, output_price = 18, 72
        else:
            input_price, output_price = 2, 6

        return (input_tokens / 1_000_000) * input_price + (
            output_tokens / 1_000_000
        ) * output_price

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
        # OpenAI 格式
        openai_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                openai_messages.append({"role": "system", "content": msg.content})
                continue

            if not msg.images:
                openai_messages.append({"role": msg.role, "content": msg.content})
                continue

            # 多模态消息
            content_parts: list[dict[str, Any]] = []
            if msg.content:
                content_parts.append({"type": "text", "text": msg.content})

            for img in msg.images:
                data_uri = f"data:{img.media_type};base64,{img.base64_data}"
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri},
                    }
                )

            openai_messages.append(
                {"role": msg.role, "content": content_parts}
            )

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": openai_messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": (
                temperature if temperature is not None else self.temperature
            ),
        }

        # MiniMax 模型: 关闭 thinking, 避免 <think> 前缀干扰 JSON 解析
        # (MiniMax-M3 支持 disabled; 仅对 MiniMax 模型生效)
        if "minimax" in self.model_name.lower():
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        # OpenAI 支持原生 JSON mode
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self.client.chat.completions.create(**kwargs)

        content_str = response.choices[0].message.content or ""

        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": (
                response.usage.completion_tokens if response.usage else 0
            ),
        }

        return ChatResponse(
            content=content_str,
            usage=usage,
            model=self.model_name,
            raw=response,
        )


class DoubaoProvider(OpenAIProvider):
    """豆包（字节跳动火山引擎）provider,继承 OpenAI 实现.

    配置示例:
        OPENAI_API_KEY=xxx (方舟 API key)
        OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
        SHU_AI_MODEL=doubao-1-5-vision-pro-32k
    """

    name = "doubao"

    def __init__(
        self,
        model_name: str = "doubao-1-5-vision-pro-32k",
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not base_url:
            base_url = "https://ark.cn-beijing.volces.com/api/v3"
        super().__init__(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            provider_kind="doubao",
            **kwargs,
        )
