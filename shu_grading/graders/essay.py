"""作文评分核心：只评价正文，学生身份由应用层管理。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..models.base import ChatMessage, ImageInput, ModelProvider
from ..models.tracing import call_model
from ..prompts_loader import load_prompt
from ..schemas import (
    CriterionScore,
    GradingResult,
    LevelEnum,
    Rubric,
)
from .json_parse import extract_json, repair_to_json

logger = logging.getLogger(__name__)


# ============ Prompt 构建 ============

# 评分指导由教师表单提供，留空时使用通用规则。
_DEFAULT_GRADING_INSTRUCTIONS = """## 评分规则

严格按本次评分标准中的维度逐项评分。
各维度分是实际得分，不得超过其满分，不要再次乘以权重。
总分等于各维度实际得分之和。每个维度需给出基于正文的评分理由。

level 等级:
- 优秀: ≥ 0.85
- 良好: 0.70 - 0.85
- 合格: 0.55 - 0.70
- 不合格: < 0.55"""


def _build_criterion_text(rubric: Rubric) -> str:
    """把 rubric 的 criteria 转成文本说明."""
    lines = []
    for c in rubric.criteria:
        max_score = c.weight * rubric.max_score
        line = f"- {c.name}(权重 {c.weight * 100:.0f}%,满分 {max_score:.1f} 分):{c.description}"
        if c.level_descriptors:
            for level, desc in c.level_descriptors.items():
                line += f"\n  - {level}:{desc}"
        lines.append(line)
    return "\n".join(lines)


def _build_criterion_details(rubric: Rubric) -> str:
    """把 rubric 的 criteria 转成结构化 JSON."""
    import json

    details = []
    for c in rubric.criteria:
        max_score = c.weight * rubric.max_score
        details.append(
            {
                "name": c.name,
                "weight": c.weight,
                "max_score": round(max_score, 2),
                "description": c.description,
                "level_descriptors": c.level_descriptors or {},
            }
        )
    return "```json\n" + json.dumps(details, ensure_ascii=False, indent=2) + "\n```"


def _build_system_prompt(rubric: Rubric) -> str:
    """构建系统 prompt."""
    return load_prompt("grading_system.txt", grade_level=rubric.grade_level or "未指定")


def _build_user_prompt(rubric: Rubric) -> str:
    """仅发送题目与评分标准，学生身份由应用管理，不发送班级名单。"""
    criteria_text = _build_criterion_text(rubric)
    criterion_details = _build_criterion_details(rubric)

    # 评分指导:老师自定义的优先,留空时用默认
    grading_instructions = rubric.essay_grading.strip() or _DEFAULT_GRADING_INSTRUCTIONS

    # Rubric 没有 title 字段,直接用 description 作为标题
    return load_prompt(
        "essay_user.txt",
        title=rubric.description or "(无)",
        description=rubric.description or "(无)",
        max_score=rubric.max_score,
        grade_level=rubric.grade_level or "未指定",
        criteria_text=criteria_text,
        criterion_details=criterion_details,
        grading_instructions=grading_instructions,
    )


# ============ 响应解析 ============

def _level_for_score(score: float, max_score: float) -> LevelEnum:
    """根据分数比例判断等级."""
    if max_score <= 0:
        return LevelEnum.QUALIFIED
    ratio = score / max_score
    if ratio >= 0.85:
        return LevelEnum.EXCELLENT
    if ratio >= 0.70:
        return LevelEnum.GOOD
    if ratio >= 0.55:
        return LevelEnum.QUALIFIED
    return LevelEnum.UNQUALIFIED


def _parse_grading_data(data: dict[str, Any], rubric: Rubric) -> GradingResult:
    """从 AI 响应的 JSON 数据构造 GradingResult."""
    criterion_scores: list[CriterionScore] = []
    for cs in data.get("criterion_scores", []):
        level_raw = cs.get("level", "")
        level = None
        if level_raw:
            for le in LevelEnum:
                if le.value in str(level_raw) or str(level_raw) in le.value:
                    level = le
                    break
        if level is None:
            level = _level_for_score(
                float(cs.get("score", 0)), float(cs.get("max_score", 1))
            )

        criterion_scores.append(
            CriterionScore(
                name=cs.get("name", "未命名"),
                score=float(cs.get("score", 0)),
                max_score=float(cs.get("max_score", 0)),
                level=level,
                comment=cs.get("comment", ""),
            )
        )

    expected = {c.name: round(c.weight * rubric.max_score, 2) for c in rubric.criteria}
    if len(expected) != len(rubric.criteria):
        raise ValueError("评分维度名称不能重复")
    if len(criterion_scores) != len(expected) or {c.name for c in criterion_scores} != set(expected):
        raise ValueError("模型返回的评分维度不完整或重复")
    import math
    for cs in criterion_scores:
        if not math.isfinite(cs.score) or not 0 <= cs.score <= expected[cs.name]:
            raise ValueError(f"{cs.name}得分超出允许范围")
        cs.max_score = expected[cs.name]
    # 只从有效维度计算总分，不信任模型单独输出的总分。
    total_score = sum(cs.score for cs in criterion_scores)

    return GradingResult(
        total_score=round(total_score, 1),
        max_score=rubric.max_score,
        criterion_scores=criterion_scores,
        overall_comment=data.get("overall_comment", ""),
        highlights=data.get("highlights", []),
        suggestions=data.get("suggestions", []),
        essay_title=data.get("essay_title", ""),
        transcription=data.get("transcription", ""),
    )


async def grade_essay(
    image_paths: list[Path | str],
    rubric: Rubric,
    model: ModelProvider,
) -> tuple[GradingResult | None, str | None]:
    """一次模型请求完成正文转写与评分，不接收学生身份。"""
    # 1. 编码图片
    images: list[ImageInput] = []
    for p in image_paths:
        try:
            img = ImageInput.from_path(p)
            images.append(img)
        except Exception as e:
            return None, f"加载图片失败 {p}: {e}"

    if not images:
        return None, "未提供有效图片"

    # 2. 构建 messages
    system_msg = _build_system_prompt(rubric)
    user_msg = _build_user_prompt(rubric)

    messages = [
        ChatMessage(role="system", content=system_msg),
        ChatMessage(role="user", content=user_msg, images=images),
    ]

    # 3. 决定 json_mode(OpenAI 兼容接口原生支持,Claude/Ollama 用 prompt 约束)
    json_mode = model.name in ("openai", "doubao", "ollama")

    # 4. 调用 AI
    try:
        response = await call_model(model,
            messages=messages,
            json_mode=json_mode,
        )
    except (TypeError, AttributeError) as e:
        logger.exception("模型客户端处理响应失败")
        return None, f"模型客户端错误: {e}"
    except Exception as e:
        logger.error(f"AI 调用失败: {e}")
        return None, f"AI 调用失败: {e}"

    # No repair call can recover text that was never generated or was cut off.
    if response.finish_reason in ('length', 'max_tokens'):
        return None, '模型输出达到上限，评分正文未完整生成。请管理员提高输出上限或降低思考档位后再重试。'
    if response.finish_reason in ('incomplete','refusal','content_filter'):
        return None, '模型未返回评分正文：响应未完成或被拒绝，请管理员检查调用记录。'
    if not response.content.strip():
        return None, '模型未返回评分正文。请管理员检查输出预算、思考档位和模型响应后再重试。'

    # 5. 解析 JSON(2 层 fallback)
    data = extract_json(response.content)
    if data is None:
        logger.debug(f"首次 JSON 解析失败,尝试修复. Raw[:200]: {response.content[:200]}")
        data = await repair_to_json(response.content, model)

    if data is None:
        logger.warning(f"AI 输出无法解析为 JSON(已尝试修复). Raw[:200]: {response.content[:200]}")
        return (
            None,
            f"AI 输出无法解析为 JSON: {response.content[:200]}...",
        )

    try:
        return _parse_grading_data(data, rubric), None
    except Exception as e:
        return None, f"解析评分数据失败: {e}"
