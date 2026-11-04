import math
import os
from contextlib import suppress
from dataclasses import replace

from pydantic import ValidationError

from ...config import load_settings
from ...graders.essay import grade_essay
from ...models.factory import create_provider
from ...schemas import GradingResult, LevelEnum, Rubric
from ..errors import GradingFailure, ServiceError
from .base import GradingInput


def validate_result(result: GradingResult, rubric: Rubric):
    expected = {c.name: c.weight * rubric.max_score for c in rubric.criteria}
    actual = result.criterion_scores
    if len(expected) != len(rubric.criteria) or len(actual) != len(expected) or {c.name for c in actual} != set(expected):
        raise ServiceError(422, "评分维度必须与本任务标准一致，且不能重复")
    for c in actual:
        if not math.isfinite(c.score) or not 0 <= c.score <= expected[c.name] + 1e-6:
            raise ServiceError(422, f"{c.name}得分超出范围")
        c.max_score = round(expected[c.name], 2)
        ratio = c.score / c.max_score if c.max_score else 0
        c.level = (LevelEnum.EXCELLENT if ratio >= .85 else LevelEnum.GOOD if ratio >= .7
                   else LevelEnum.QUALIFIED if ratio >= .55 else LevelEnum.UNQUALIFIED)
    if not math.isfinite(result.presentation_deduction) or not 0 <= result.presentation_deduction <= rubric.max_score:
        raise ServiceError(422, "卷面扣分不能超过本题满分")
    result.total_score = round(max(0, sum(c.score for c in actual) - result.presentation_deduction), 1)
    result.max_score = rubric.max_score
    return result


class EssayGrader:
    task_type = "essay"
    version = "1"
    title = "作文阅卷"

    def __init__(self, provider_factory=None):
        self.provider_factory = provider_factory

    def validate_configuration(self, configuration):
        try:
            rubric = Rubric.model_validate(configuration["rubric"])
        except (KeyError, ValidationError, TypeError):
            raise ServiceError(422, "请填写完整的作文评分标准")
        names = [c.name.strip() for c in rubric.criteria]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise ServiceError(422, "评分维度名称不能为空或重复")
        return {"rubric": rubric.model_dump(mode="json")}

    def validate_review(self, configuration, result):
        return validate_result(result, Rubric.model_validate(configuration["rubric"]))

    async def grade(self, request: GradingInput):
        provider = None
        try:
            if self.provider_factory:
                provider = self.provider_factory()
            else:
                settings = load_settings()
                settings = replace(settings, openai_base_url=settings.openai_base_url or None,
                                   max_tokens=int(os.getenv("SHU_WEB_MAX_TOKENS", str(max(settings.max_tokens, 6000)))))
                provider = create_provider(settings)
            if provider.validate_config():
                raise GradingFailure("请管理员检查服务器模型配置")
            rubric = Rubric.model_validate(request.configuration["rubric"])
            rubric.description = request.assignment
            result, error = await grade_essay(list(request.image_paths), rubric, provider)
            if error or result is None:
                if error and error.startswith("模型客户端错误:"):
                    raise GradingFailure("模型响应处理失败，请管理员检查服务器依赖后重试")
                detail = (error or '').lower()
                permanent = any(marker in detail for marker in ('400','401','403','404','authentication',
                    'invalid_api_key','model_not_found','permission','context_length_exceeded','unsupported'))
                raise GradingFailure("未能取得有效评分，请重试或联系管理员",
                                     retryable=bool(error and "AI 调用失败" in error and not permanent))
            result.presentation_deduction = 0
            return self.validate_review(request.configuration, result)
        except (GradingFailure, ServiceError):
            raise
        except Exception as exc:
            raise GradingFailure("评分服务异常，请管理员检查模型配置") from exc
        finally:
            if provider is not None and hasattr(provider, "aclose"):
                with suppress(Exception):
                    await provider.aclose()
