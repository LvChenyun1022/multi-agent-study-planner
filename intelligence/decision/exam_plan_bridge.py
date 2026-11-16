# -*- coding: utf-8 -*-
"""exam_plan 桥接：student_state + CourseSpec → run_exam_plan() 标准输入。

映射规则：
- knowledge：课程声明的知识点权重 × 画像中的掌握度
  （无证据知识点使用中性先验 0.5，is_prior 语义见 profile_builder）；
- n_diag：该课程各知识点有效样本量之和（四舍五入），
  让 1.0 排程引擎的不确定性因子 U_i 随证据积累自然下降；
- 其余考试参数（exam_day / credits / degree / priority / a / b /
  min_hours / tasks）原样取自 CourseSpec。

输出字典与 agents/scheduler_adapter.py 的校验规则完全对齐，
可直接传入 run_exam_plan()。
"""
from __future__ import annotations

from schemas.course import CourseSpec
from schemas.learning_event import EventValidationError


def build_exam_plan(student_state: dict, course_specs: list,
                    daily_hours, start_day: int = 0,
                    risk_weights: list | None = None) -> dict:
    if not isinstance(student_state, dict) or "courses" not in student_state:
        raise EventValidationError("student_state 必须是画像字典")
    if not course_specs:
        raise EventValidationError("course_specs 不能为空")

    state_courses = student_state.get("courses", {})
    courses_payload: list = []
    for spec in course_specs:
        if not isinstance(spec, CourseSpec):
            raise EventValidationError("course_specs 元素必须是 CourseSpec")
        spec.validate()
        kp_state = state_courses.get(spec.course_id, {}).get("knowledge", {})

        knowledge = []
        effective_n_total = 0.0
        for kp, weight in spec.knowledge_weights.items():
            entry = kp_state.get(kp)
            mastery = entry["mastery"] if entry else 0.5
            effective_n_total += (entry or {}).get("effective_n", 0.0)
            knowledge.append({
                "name": kp,
                "weight": weight,
                "mastery": mastery,
            })

        courses_payload.append({
            "name": spec.name,
            "exam_day": spec.exam_day,
            "credits": spec.credits,
            "degree": spec.degree,
            "priority": spec.priority,
            "n_diag": int(round(effective_n_total)),
            "a": spec.a,
            "b": spec.b,
            "min_hours": spec.min_hours,
            "knowledge": knowledge,
            "tasks": [
                {
                    "name": task.name,
                    "kp": task.kp,
                    "hours": task.hours,
                    "required": task.required,
                    "deps": list(task.deps),
                }
                for task in spec.tasks
            ],
        })

    plan = {
        "courses": courses_payload,
        "daily_hours": daily_hours,
        "start_day": int(start_day),
    }
    if risk_weights is not None:
        plan["risk_weights"] = list(risk_weights)
    return plan
