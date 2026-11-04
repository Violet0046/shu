"""HTTP 请求数据，保留现有前端协议。"""
from pydantic import BaseModel, Field, model_validator
from ...schemas import Rubric, GradingResult

class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class StudentInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    student_id: str = Field(min_length=1, max_length=80)


class ClassInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    students: list[StudentInput] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_ids(self):
        self.name = self.name.strip()
        for student in self.students:
            student.name = student.name.strip()
            student.student_id = student.student_id.strip()
        if not self.name or any(not s.name or not s.student_id for s in self.students):
            raise ValueError("班级、姓名和学号不能为空")
        if len({s.student_id for s in self.students}) != len(self.students):
            raise ValueError("学生学号不能重复")
        return self


class TemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rubric: Rubric

    @model_validator(mode="after")
    def valid_names(self):
        names = [c.name.strip() for c in self.rubric.criteria]
        if not self.name.strip() or any(not n for n in names) or len(set(names)) != len(names):
            raise ValueError("模板和维度名称不能为空，维度不能重复")
        return self


class TaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    assignment: str = Field(min_length=1, max_length=20000)
    mode: str = "anonymous"
    class_id: str | None = None
    rubric: Rubric | None = None
    task_type: str = "essay"
    configuration: dict | None = None


class SubmissionInput(BaseModel):
    image: str = Field(max_length=21_000_000)
    student_id: str | None = None
    paper_number: str = Field(default="", max_length=100)
    request_id: str | None = Field(default=None, min_length=8, max_length=128)


class ReviewInput(BaseModel):
    result: GradingResult
    confirm: bool = False
    revision: int = Field(ge=0)


