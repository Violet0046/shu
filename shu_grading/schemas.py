"""评分标准、统一评分结果和成绩报告的数据结构。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


# ============ 评分相关枚举 ============

class LevelEnum(str, Enum):
    """评分等级."""
    EXCELLENT = "优秀"
    GOOD = "良好"
    QUALIFIED = "合格"
    UNQUALIFIED = "不合格"


class GradingStatus(str, Enum):
    SUCCESS = "success"
    AI_FAILED = "ai_failed"


class Criterion(BaseModel):
    """单个评分维度."""
    name: str = Field(..., description="维度名称")
    weight: float = Field(..., ge=0, le=1)
    description: str
    level_descriptors: dict[str, str] | None = None


class Rubric(BaseModel):
    """教师表单创建的评分标准。"""
    description: str = Field(
        default="", description="评分标准整体说明(可选)"
    )
    max_score: float = Field(..., gt=0, le=100)
    grade_level: str | None = None
    criteria: list[Criterion] = Field(..., min_length=1)
    # 评分指导(老师可自定义)
    # 留空时使用 grader 内置的默认评分哲学
    essay_grading: str = Field(
        default="",
        description="教师在评分表单中自定义的评分指导与特殊规则",
    )

    @field_validator("criteria")
    @classmethod
    def _validate_weights(cls, v: list[Criterion]) -> list[Criterion]:
        total = sum(c.weight for c in v)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"所有 criteria 权重之和必须为 1.0,当前为 {total:.4f}")
        return v


class StudentIdentity(BaseModel):
    """教师指定的报告显示身份。"""
    name: str


class CriterionScore(BaseModel):
    """单个维度评分."""
    name: str
    score: float
    max_score: float
    level: LevelEnum | None = None
    comment: str = ""


class GradingResult(BaseModel):
    """单篇作文评分结果."""
    total_score: float
    max_score: float
    criterion_scores: list[CriterionScore] = Field(default_factory=list)
    overall_comment: str = ""
    highlights: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    word_count: int | None = None
    transcription: str = Field(default="", description="忠实转写正文，疑字标记，不润色")
    presentation_deduction: float = Field(default=0, ge=0, le=100, description="教师卷面扣分，AI 不填写")

class ReportRecord(BaseModel):
    """单份答卷的成绩报告记录。"""
    student: StudentIdentity
    status: GradingStatus
    grading: GradingResult | None = None
    error_message: str | None = None
    needs_review: bool = False


class GradingSummary(BaseModel):
    """班级阅卷汇总."""
    total_students: int
    graded_count: int = 0
    failed_count: int = 0
    needs_review_count: int = 0
    average_score: float | None = None
    max_score_actual: float | None = None
    min_score_actual: float | None = None
    distribution: dict[str, int] = Field(default_factory=dict)


class GradingReport(BaseModel):
    """一批阅卷的完整结果."""
    summary: GradingSummary
    results: list[ReportRecord] = Field(default_factory=list)
    assignment_description: str = ""
    class_name: str = ""
