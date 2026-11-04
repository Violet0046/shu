"""Prompt 加载器.

从 shu_grading/prompts/ 目录加载 prompt 模板,支持 {variable} 占位符替换。
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str, **kwargs: object) -> str:
    """加载 prompt 模板并替换变量.

    Args:
        name: 文件名,如 'essay_grading.txt'
        kwargs: 模板变量,如 title='...', max_score=50

    Returns:
        替换变量后的字符串
    """
    path = _PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt 模板不存在: {path}")

    template = path.read_text(encoding="utf-8")

    # 用 str.format 替换,缺失的变量用占位符提示
    try:
        return template.format(**kwargs)
    except KeyError as e:
        # 缺少变量时,保留原占位符
        return template.replace("{" + str(e).strip("'") + "}", f"<MISSING:{e}>")
