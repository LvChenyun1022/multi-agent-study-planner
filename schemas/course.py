# -*- coding: utf-8 -*-
"""CourseSpec：课程档案（ExamOS 2.0 Phase 1）。

保存课程的基本考试信息与知识点权重声明，是连接
「学生画像」与「run_exam_plan() 排程输入」的桥接数据源。

校验规则与 agents/scheduler_adapter.py 保持一致，
确保本层产出的数据可以无损进入 1.0 排程引擎。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .learning_event import EventValidationError


def _number(value, field_name: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise EventValidationError(f"{field_name} 必须是数字，不能是布尔值")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise EventValidationError(f"{field_name} 必须是数字") from None
    if result != result or result in (float("inf"), float("-inf")):
        raise EventValidationError(f"{field_name} 必须是有限数字")
    if minimum is not None and result < minimum:
        raise EventValidationError(f"{field_name} 不能小于 {minimum}")
    if maximum is not None and result > maximum:
        raise EventValidationError(f"{field_name} 不能大于 {maximum}")
    return result


def _integer(value, field_name: str, *, minimum: int = 0) -> int:
    number = _number(value, field_name, minimum=minimum)
    if not number.is_integer():
        raise EventValidationError(f"{field_name} 必须是整数")
    return int(number)


@dataclass
class TaskSpec:
    """课程内一个可排程的学习任务声明。"""

    name: str
    kp: str
    hours: float
    required: bool
    deps: list = field(default_factory=list)

    def validate(self, prefix: str = "task") -> "TaskSpec":
        if not str(self.name).strip() or not str(self.kp).strip():
            raise EventValidationError(f"{prefix}.name 和 {prefix}.kp 不能为空")
        self.hours = _number(self.hours, f"{prefix}.hours", minimum=0.0)
        if not isinstance(self.required, bool):
            raise EventValidationError(f"{prefix}.required 必须是 true/false")
        if not isinstance(self.deps, list) or not all(
            isinstance(dep, str) for dep in self.deps
        ):
            raise EventValidationError(f"{prefix}.deps 必须是字符串数组")
        return self


@dataclass
class CourseSpec:
    """一门课程的完整档案。

    knowledge_weights：知识点 → 考试权重（相对值，求和不必为 1，
    1.0 排程引擎内部会按 Σw 归一化）。
    """

    course_id: str
    name: str
    exam_day: int
    credits: float = 3.0
    degree: bool = True
    priority: float = 0.5
    a: float = 2.0
    b: float = 0.5
    min_hours: float = 0.0
    knowledge_weights: dict = field(default_factory=dict)
    tasks: list = field(default_factory=list)

    def validate(self) -> "CourseSpec":
        if not str(self.course_id).strip():
            raise EventValidationError("course_id 不能为空")
        if not str(self.name).strip():
            raise EventValidationError(f"课程 {self.course_id} 的 name 不能为空")
        self.exam_day = _integer(self.exam_day, f"{self.name}.exam_day", minimum=1)
        self.credits = _number(self.credits, f"{self.name}.credits", minimum=0.000001)
        if not isinstance(self.degree, bool):
            raise EventValidationError(f"{self.name}.degree 必须是 true/false")
        self.priority = _number(self.priority, f"{self.name}.priority",
                                minimum=0.0, maximum=1.0)
        self.a = _number(self.a, f"{self.name}.a", minimum=0.000001)
        self.b = _number(self.b, f"{self.name}.b", minimum=0.000001)
        self.min_hours = _number(self.min_hours, f"{self.name}.min_hours",
                                 minimum=0.0)

        if not isinstance(self.knowledge_weights, dict) or not self.knowledge_weights:
            raise EventValidationError(f"{self.name}.knowledge_weights 必须是非空对象")
        cleaned = {}
        for kp, weight in self.knowledge_weights.items():
            if not str(kp).strip():
                raise EventValidationError(f"{self.name} 的知识点名称不能为空")
            cleaned[str(kp)] = _number(
                weight, f"{self.name}.knowledge_weights[{kp}]", minimum=0.0
            )
        if sum(cleaned.values()) <= 0:
            raise EventValidationError(f"{self.name} 的知识点权重之和必须大于 0")
        self.knowledge_weights = cleaned

        if not isinstance(self.tasks, list) or not self.tasks:
            raise EventValidationError(f"{self.name}.tasks 必须是非空数组")
        names = set()
        for index, task in enumerate(self.tasks):
            if not isinstance(task, TaskSpec):
                raise EventValidationError(
                    f"{self.name}.tasks[{index}] 必须是 TaskSpec"
                )
            task.validate(f"{self.name}.tasks[{index}]")
            if task.name in names:
                raise EventValidationError(
                    f"课程 {self.name} 内任务名称重复：{task.name}"
                )
            names.add(task.name)
        for task in self.tasks:
            missing = [dep for dep in task.deps if dep not in names]
            if missing:
                raise EventValidationError(
                    f"课程 {self.name} 的任务 {task.name} 依赖不存在的任务："
                    + ", ".join(missing)
                )
            if task.kp not in self.knowledge_weights:
                raise EventValidationError(
                    f"课程 {self.name} 的任务 {task.name} 关联了未声明的知识点："
                    f"{task.kp}"
                )
        self._check_cycles()
        return self

    def _check_cycles(self) -> None:
        graph = {task.name: task.deps for task in self.tasks}
        visiting: set = set()
        visited: set = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise EventValidationError(
                    f"课程 {self.name} 的任务依赖存在循环：{name}"
                )
            if name in visited:
                return
            visiting.add(name)
            for dep in graph[name]:
                visit(dep)
            visiting.remove(name)
            visited.add(name)

        for name in graph:
            visit(name)
