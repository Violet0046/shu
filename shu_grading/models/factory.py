"""AI Provider 工厂.

根据配置创建合适的 provider 实例。
"""

from __future__ import annotations

import logging

from ..config import Settings
from .base import ModelProvider
from .claude import ClaudeProvider
from .ollama import OllamaProvider
from .openai import DoubaoProvider, OpenAIProvider

logger = logging.getLogger(__name__)


def create_provider(settings: Settings) -> ModelProvider:
    """根据配置创建 AI provider."""
    provider_name = settings.ai_provider
    model_name = settings.ai_model
    logger.info(f"创建 AI provider: {provider_name}/{model_name}")

    if provider_name == "claude":
        return ClaudeProvider(
            model_name=model_name,
            api_key=settings.anthropic_api_key,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    elif provider_name == "ollama":
        return OllamaProvider(
            model_name=model_name,
            host=settings.ollama_host,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    elif provider_name == "doubao":
        return DoubaoProvider(
            model_name=model_name,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    elif provider_name == "openai":
        return OpenAIProvider(
            reasoning_effort=settings.reasoning_effort,
            model_name=model_name,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        )
    else:
        raise ValueError(f"不支持的 AI provider: {provider_name}")


__all__ = [
    "ModelProvider",
    "ClaudeProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "DoubaoProvider",
    "create_provider",
]
