"""AI Provider 抽象基类.

所有具体 provider (Claude / Ollama / OpenAI / 豆包) 都必须实现 ModelProvider 接口。
这样上层业务逻辑（评分、OCR）不需要关心底层模型细节。
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ImageInput:
    """图片输入."""
    path: Path | None = None
    base64_data: str | None = None
    media_type: str = "image/jpeg"  # image/jpeg, image/png, image/webp, image/gif

    @classmethod
    def from_path(cls, path: Path | str) -> "ImageInput":
        """从文件路径创建,自动读取并编码为 base64."""
        p = Path(path)
        suffix = p.suffix.lower()
        media_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(suffix, "image/jpeg")

        with open(p, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")

        return cls(path=p, base64_data=data, media_type=media_type)


@dataclass
class ChatMessage:
    """单条对话消息."""
    role: str  # "system" | "user" | "assistant"
    content: str
    images: list[ImageInput] = field(default_factory=list)


@dataclass
class ChatResponse:
    """模型回复."""
    content: str
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    raw: Any = None  # 原始响应,用于调试


class ModelProvider(ABC):
    """AI Provider 抽象基类."""

    name: str = "base"

    def __init__(self, model_name: str, **kwargs: Any) -> None:
        self.model_name = model_name

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """发送对话请求.

        Args:
            messages: 消息列表(system / user / assistant 交替)
            json_mode: 是否强制 JSON 输出
            temperature: 覆盖默认 temperature
            max_tokens: 覆盖默认 max_tokens
        """



    def validate_config(self) -> list[str]:
        """检查 provider 配置,返回错误信息列表."""
        return []

    async def aclose(self) -> None:
        """释放长时间运行的网页服务中每次评分使用的客户端连接。"""
        import inspect
        client = getattr(self, "client", None)
        close = getattr(client, "close", None) or getattr(client, "aclose", None)
        if close:
            result = close()
            if inspect.isawaitable(result):
                await result
