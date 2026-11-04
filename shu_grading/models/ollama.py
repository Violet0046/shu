"""Ollama 本地模型 provider.

Ollama 通过 HTTP API 调用本地模型,无需 API key。
推荐模型: qwen2-vl:7b (中文视觉)、llama3.2-vision:11b
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import ChatMessage, ChatResponse, ModelProvider

logger = logging.getLogger(__name__)


class OllamaProvider(ModelProvider):
    """Ollama 本地模型 provider."""

    name = "ollama"

    def __init__(
        self,
        model_name: str = "qwen2-vl:7b",
        host: str = "http://localhost:11434",
        max_tokens: int = 2048,
        temperature: float = 0.1,
        **kwargs: Any,
    ) -> None:
        super().__init__(model_name, **kwargs)
        self.host = host.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature

    def supports_vision(self) -> bool:
        """Ollama 是否支持视觉取决于具体模型."""
        vision_keywords = ("vl", "vision", "llava", "minicpm-v")
        return any(kw in self.model_name.lower() for kw in vision_keywords)

    def validate_config(self) -> list[str]:
        """检查 Ollama 服务是否可达."""
        errors: list[str] = []
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self.host}/api/tags")
                if r.status_code != 200:
                    errors.append(
                        f"Ollama 服务不可达: HTTP {r.status_code} from {self.host}"
                    )
                else:
                    # 检查模型是否已下载
                    tags = r.json().get("models", [])
                    model_names = [m.get("name", "") for m in tags]
                    # 检查是否有匹配的模型(可能是 tag 后缀不同)
                    if not any(
                        self.model_name in n or n.startswith(self.model_name)
                        for n in model_names
                    ):
                        errors.append(
                            f"模型 {self.model_name} 未在 Ollama 中找到。"
                            f"已安装: {model_names}。"
                            f"运行 'ollama pull {self.model_name}' 下载。"
                        )
        except httpx.ConnectError:
            errors.append(
                f"无法连接 Ollama 服务 ({self.host})。"
                f"请确认 Ollama 已启动: https://ollama.com/download"
            )
        except Exception as e:
            errors.append(f"Ollama 配置检查失败: {e}")

        return errors

    def estimate_cost(self, messages: list[ChatMessage]) -> float:
        """本地运行,无 API 费用.返回 0."""
        return 0.0

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.ConnectError, TimeoutError)),
        reraise=True,
    )
    async def chat(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """调用 Ollama /api/chat 端点."""
        # Ollama 消息格式
        ollama_messages: list[dict[str, Any]] = []

        for msg in messages:
            ollama_msg: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.images:
                # Ollama 接受 images 字段(图片 base64 数组)
                ollama_msg["images"] = [
                    img.base64_data for img in msg.images if img.base64_data
                ]
            ollama_messages.append(ollama_msg)

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "temperature": (
                    temperature if temperature is not None else self.temperature
                ),
                "num_predict": max_tokens or self.max_tokens,
            },
        }

        if json_mode:
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{self.host}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content_str = data.get("message", {}).get("content", "")

        # Ollama 返回的 usage 字段
        usage = {
            "input_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": data.get("eval_count", 0),
        }

        return ChatResponse(
            content=content_str,
            usage=usage,
            model=self.model_name,
            raw=data,
        )


def encode_image_base64(path: str | bytes) -> str:
    """辅助函数:把图片编码为 base64."""
    if isinstance(path, str):
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    return base64.standard_b64encode(path).decode("utf-8")
