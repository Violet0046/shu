"""配置加载.

从服务器环境变量和本地 .env 文件加载模型与数据目录配置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

def load_environment():
    """安装后从工作目录或指定文件读取；系统环境变量优先。"""
    explicit = os.getenv("SHU_ENV_FILE")
    path = Path(explicit).expanduser() if explicit else Path.cwd() / ".env"
    if explicit and not path.is_file():
        raise FileNotFoundError(f"SHU_ENV_FILE 指定的配置文件不存在：{path}")
    load_dotenv(path, override=False)


load_environment()


ProviderName = Literal["claude", "openai", "ollama", "doubao"]


@dataclass
class Settings:
    """全局配置."""

    # AI Provider
    ai_provider: ProviderName = "claude"
    ai_model: str = "claude-sonnet-4-5"

    # API Keys (按 provider 选填)
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    ollama_host: str = "http://localhost:11434"

    # 通用
    max_tokens: int = 2048
    temperature: float = 0.1

    # 数据目录
    data_dir: Path = field(default_factory=lambda: Path.home() / ".shu-grading")



def load_settings() -> Settings:
    """从环境变量加载配置."""
    provider = os.getenv("SHU_AI_PROVIDER", "claude").lower()
    if provider not in ("claude", "openai", "ollama", "doubao"):
        provider = "claude"

    data_dir = Path(os.getenv("SHU_DATA_DIR", "~/.shu-grading")).expanduser()

    return Settings(
        ai_provider=provider,  # type: ignore[arg-type]
        ai_model=os.getenv("SHU_AI_MODEL", "claude-sonnet-4-5"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL"),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        max_tokens=int(os.getenv("SHU_MAX_TOKENS", "2048")),
        temperature=float(os.getenv("SHU_TEMPERATURE", "0.1")),
        data_dir=data_dir,
    )
