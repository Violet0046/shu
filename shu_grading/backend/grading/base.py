"""每种题型只需实现一次答卷的评分，账户、队列和复核共用。"""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...schemas import GradingResult
from ..errors import ServiceError


@dataclass(frozen=True)
class GradingInput:
    assignment: str
    image_paths: tuple[Path, ...]
    configuration: dict
    # 不包含姓名、班级、名单或账户信息。


class Grader(Protocol):
    task_type: str
    version: str
    title: str

    def validate_configuration(self, configuration: dict) -> dict: ...
    async def grade(self, request: GradingInput) -> GradingResult: ...
    def validate_review(self, configuration: dict, result: GradingResult) -> GradingResult: ...


class GraderRegistry:
    def __init__(self):
        self._graders: dict[tuple[str, str], Grader] = {}

    def register(self, grader: Grader):
        key = (grader.task_type, grader.version)
        if key in self._graders:
            raise ValueError(f"重复的阅卷实现：{key}")
        self._graders[key] = grader

    def resolve(self, task_type: str, version: str | None = None) -> Grader:
        candidates = [g for (t, _), g in self._graders.items() if t == task_type]
        if version is None and candidates:
            return candidates[-1]
        grader = self._graders.get((task_type, version))
        if not grader:
            raise ServiceError(422, "该题型或评分版本尚未开放")
        return grader

    def available(self):
        return [{"task_type": g.task_type, "version": g.version, "title": g.title}
                for g in self._graders.values()]
