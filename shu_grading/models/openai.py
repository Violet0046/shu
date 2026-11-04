"""OpenAI 兼容 provider.

支持 OpenAI、Azure OpenAI、DeepSeek、豆包（通过火山引擎方舟）等所有 OpenAI 兼容接口。
"""

from __future__ import annotations

import logging
from typing import Any


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
        reasoning_effort: str = "low",
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name, **kwargs)
        if not api_key:
            raise ValueError(
                "OpenAIProvider 需要 API key."
                " 请设置 OPENAI_API_KEY 或传入 api_key 参数。"
            )
        if reasoning_effort not in ('low','medium','high','xhigh','max'):
            raise ValueError('SHU_REASONING_EFFORT 必须为 low/medium/high/xhigh/max')
        self.reasoning_effort = reasoning_effort
        from openai import AsyncOpenAI

        # Avoid optional Brotli decoders incompatible with some SDK transports.
        self.client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=0,
            default_headers={"Accept-Encoding": "gzip, deflate"},
        )
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature


    def validate_config(self) -> list[str]:
        errors: list[str] = []
        if not self.client:
            errors.append("OpenAI client 未初始化")
        return errors


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

        # M3.1-Flash requires adaptive thinking; never inherit M3's opt-out.
        # Keep other model families on their own defaults.
        model_id = self.model_name.lower()
        if model_id == "minimax-m3.1-flash":
            kwargs["extra_body"] = {"thinking": {"type": "adaptive"}}
            kwargs["reasoning_effort"] = self.reasoning_effort
        elif model_id == "minimax-m3":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        # OpenAI 支持原生 JSON mode
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self.client.chat.completions.create(**kwargs)

        content_str = response.choices[0].message.content or ""

        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else None,
            "output_tokens": (
                response.usage.completion_tokens if response.usage else None
            ),
        }

        return ChatResponse(
            content=content_str,
            usage=usage,
            model=self.model_name,
            raw=response,
            finish_reason=response.choices[0].finish_reason,
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
            **kwargs,
        )
