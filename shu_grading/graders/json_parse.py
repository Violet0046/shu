"""JSON 解析辅助.

AI 模型有时会在 JSON 外面加 ```json ``` 包裹,或加一些解释性文字。
这个模块提供稳健的 JSON 提取能力。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def extract_json(text: str) -> dict[str, Any] | None:
    """从任意文本中提取 JSON 对象.

    策略(按优先级):
    1. 直接 json.loads(整段文本)
    2. ```json ... ``` 代码块(遍历所有匹配)
    3. 贪婪匹配 { ... }(包括所有可能的尝试)
    4. 常见错误修复
    """
    if not text:
        return None

    text = text.strip()

    # 剥离 <think>...</think> 思考前缀 (部分模型如 MiniMax/DeepSeek 会输出)
    think_pattern = re.compile(r"<think>.*?</think>", re.DOTALL)
    text = think_pattern.sub("", text).strip()

    # 1. 直接尝试整段解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and result:
            for item in result:
                if isinstance(item, dict):
                    return item
        return None
    except json.JSONDecodeError:
        pass

    # 2. 提取 ```json ... ``` 或 ``` ... ``` 代码块(遍历所有匹配)
    code_block_pattern = re.compile(
        r"```(?:json)?\s*\n?(.*?)\n?```",
        re.DOTALL,
    )
    for match in code_block_pattern.finditer(text):
        inner = match.group(1).strip()
        # 直接尝试
        try:
            result = json.loads(inner)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            # 尝试修复
            repaired = _try_repair_json(inner)
            if repaired:
                try:
                    result = json.loads(repaired)
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    continue

    # 3. 贪婪匹配第一个完整的 JSON 对象
    first_brace = text.find("{")
    if first_brace != -1:
        last_brace = text.rfind("}")
        if last_brace > first_brace:
            candidate = text[first_brace : last_brace + 1]

            # 尝试解析
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

            # 尝试修复
            repaired = _try_repair_json(candidate)
            if repaired:
                try:
                    result = json.loads(repaired)
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    pass

    # 4. 找第一个未闭合的 { 并尝试裁剪到合适位置
    # (有时 AI 截断输出,我们尝试从第一个 { 开始递增解析)
    if first_brace != -1:
        # 找到所有 } 的位置,尝试最近的
        close_positions = [i for i, ch in enumerate(text) if ch == "}"]
        for pos in reversed(close_positions):
            if pos <= first_brace:
                break
            candidate = text[first_brace : pos + 1]
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue

    return None


def _try_repair_json(text: str) -> str | None:
    """尝试修复常见的 JSON 错误."""
    # 移除尾部逗号
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 修复未转义的换行符（在字符串内）
    # 简单处理:把字符串内的真换行替换为 \n
    def fix_string_literal(match: re.Match) -> str:
        s = match.group(0)
        # 只处理 " ... " 字符串字面量
        content = s[1:-1]
        content = content.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return f'"{content}"'

    text = re.sub(r'"(?:[^"\\]|\\.)*"', fix_string_literal, text)
    return text


async def repair_to_json(raw_text: str, model_provider: Any) -> dict[str, Any] | None:
    """用 AI 修复无法解析的 JSON.

    发送给 AI 让它把非 JSON 内容转为合法 JSON。
    """
    from ..models.base import ChatMessage

    prompt = f"""以下是一段 AI 的输出,无法被解析为合法 JSON(可能是因为引号未转义、截断、或格式错误)。

**你的任务**:把它修复成严格合法的 JSON。

**关键要求**:
1. 输出必须是**完整** JSON,**不能截断**(所有 `{{` 都要有对应 `}}` ,所有字段都要写完)
2. 字符串内的引号 `\"` 必须转义为 `\\\\"`
3. 字符串内的换行必须转义为 `\\n`
4. 不要任何解释性文字,只输出 JSON
5. 不要 markdown 代码块包裹(不要输出 ```json ... ```)

**需要保留的字段**(从原始输出提取):
- identity.raw_name, identity.matched_name, identity.match_confidence, identity.candidates
- total_score, criterion_scores, overall_comment, highlights, suggestions, word_count

**原始输出**:
```
{raw_text[:3000]}
```

请只输出合法 JSON(从 {{ 开始,到 }} 结束):"""

    try:
        response = await model_provider.chat(
            messages=[
                ChatMessage(
                    role="system",
                    content="你是 JSON 修复助手。严格输出合法 JSON。",
                ),
                ChatMessage(role="user", content=prompt),
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=2000,
        )
        return extract_json(response.content)
    except Exception as e:
        logger.warning(f"JSON 修复失败: {e}")
        return None
