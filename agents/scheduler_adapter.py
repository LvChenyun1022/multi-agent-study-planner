# -*- coding: utf-8 -*-
"""ExamOS 排程适配层：把 JSON 化考试计划转换为 scheduler.py 的输入。

输入由 PlannerAgent 从 user_profile["exam_plan"] 读取。
daily_hours 表示扣除课程、运动、日历事件后的净可用学习时间；
算法只使用这一确定性预算，不会自行猜测学生空闲时间。

坐标系契约：spec 中的 exam_day 与 daily_hours 的日期键均为
相对规划原点 day 0 的绝对日序号。scheduler.allocate 使用绝对坐标
（day >= exam_day 起不可再排该课）；scheduler.risk_scores 的 D 因子
需要"距考试剩余天数"，因此每次评分前用 _risk_view 换算为相对视图，
绝不在 Course 上原地改写 exam_day——否则 v2 重规划会把
"剩余天数"误当"绝对日序号"，错误封掉考试前一天（start_day>0 时）。
"""
from __future__ import annotations

import math
from typing import Any

from scheduler import (
    Course, Task, allocate, apply_day_completion, conservation_check,
    hours_by_day, risk_scores, rollback_future,
)


class SchedulerInputError(ValueError):
    """考试排程输入缺字段、类型错误或约束不合法。"""


def _number(value: Any, field: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise SchedulerInputError(f"{field} 必须是数字，不能是布尔值")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise SchedulerInputError(f"{field} 必须是数字") from None
    if not math.isfinite(result):
        raise SchedulerInputError(f"{field} 必须是有限数字")
    if minimum is not None and result < minimum:
        raise SchedulerInputError(f"{field} 不能小于 {minimum}")
    if maximum is not None and result > maximum:
        raise SchedulerInputError(f"{field} 不能大于 {maximum}")
    return result


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    number = _number(value, field, minimum=minimum)
    if not number.is_integer():
        raise SchedulerInputError(f"{field} 必须是整数")
    return int(number)


def _parse_courses(raw_courses: Any) -> list[Course]:
    if not isinstance(raw_courses, list) or not raw_courses:
        raise SchedulerInputError("exam_plan.courses 必须是非空数组")

    courses: list[Course] = []
    course_names: set[str] = set()
    for index, raw in enumerate(raw_courses):
        prefix = f"courses[{index}]"
        if not isinstance(raw, dict):
            raise SchedulerInputError(f"{prefix} 必须是 JSON 对象")

        name = str(raw.get("name", "")).strip()
        if not name:
            raise SchedulerInputError(f"{prefix}.name 不能为空")
        if name in course_names:
            raise SchedulerInputError(f"课程名称重复：{name}")
        course_names.add(name)

        exam_day = _integer(raw.get("exam_day"), f"{prefix}.exam_day", minimum=1)
        credits = _number(raw.get("credits"), f"{prefix}.credits", minimum=0.000001)
        degree = raw.get("degree")
        if not isinstance(degree, bool):
            raise SchedulerInputError(f"{prefix}.degree 必须是 true/false")
        priority = _number(raw.get("priority"), f"{prefix}.priority",
                           minimum=0.0, maximum=1.0)
        n_diag = _integer(raw.get("n_diag"), f"{prefix}.n_diag", minimum=0)
        a = _number(raw.get("a"), f"{prefix}.a", minimum=0.000001)
        b = _number(raw.get("b"), f"{prefix}.b", minimum=0.000001)
        min_hours = _number(raw.get("min_hours", 0), f"{prefix}.min_hours",
                            minimum=0.0)

        raw_knowledge = raw.get("knowledge", [])
        if not isinstance(raw_knowledge, list):
            raise SchedulerInputError(f"{prefix}.knowledge 必须是数组")
        knowledge = []
        for k_index, item in enumerate(raw_knowledge):
            k_prefix = f"{prefix}.knowledge[{k_index}]"
            if not isinstance(item, dict):
                raise SchedulerInputError(f"{k_prefix} 必须是 JSON 对象")
            kp_name = str(item.get("name", "")).strip()
            if not kp_name:
                raise SchedulerInputError(f"{k_prefix}.name 不能为空")
            weight = _number(item.get("weight"), f"{k_prefix}.weight", minimum=0.0)
            mastery = _number(item.get("mastery"), f"{k_prefix}.mastery",
                              minimum=0.0, maximum=1.0)
            knowledge.append((kp_name, weight, mastery))

        raw_tasks = raw.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise SchedulerInputError(f"{prefix}.tasks 必须是非空数组")
        tasks: list[Task] = []
        task_names: set[str] = set()
        for t_index, item in enumerate(raw_tasks):
            t_prefix = f"{prefix}.tasks[{t_index}]"
            if not isinstance(item, dict):
                raise SchedulerInputError(f"{t_prefix} 必须是 JSON 对象")
            task_name = str(item.get("name", "")).strip()
            kp = str(item.get("kp", "")).strip()
            if not task_name or not kp:
                raise SchedulerInputError(f"{t_prefix}.name 和 kp 不能为空")
            if task_name in task_names:
                raise SchedulerInputError(f"课程 {name} 内任务名称重复：{task_name}")
            task_names.add(task_name)
            required = item.get("required")
            if not isinstance(required, bool):
                raise SchedulerInputError(f"{t_prefix}.required 必须是 true/false")
            deps = item.get("deps", [])
            if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
                raise SchedulerInputError(f"{t_prefix}.deps 必须是字符串数组")
            hours = _number(item.get("hours"), f"{t_prefix}.hours", minimum=0.0)
            tasks.append(Task(task_name, kp, hours, required, deps=list(deps)))

        for task in tasks:
            missing = [dep for dep in task.deps if dep not in task_names]
            if missing:
                raise SchedulerInputError(
                    f"课程 {name} 的任务 {task.name} 依赖不存在的任务：{', '.join(missing)}"
                )

        # scheduler.py 以任务名解析依赖；提前检测环，避免任务永远不可调度。
        graph = {task.name: task.deps for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_name: str) -> None:
            if task_name in visiting:
                raise SchedulerInputError(f"课程 {name} 的任务依赖存在循环：{task_name}")
            if task_name in visited:
                return
            visiting.add(task_name)
            for dependency in graph[task_name]:
                visit(dependency)
            visiting.remove(task_name)
            visited.add(task_name)

        for task_name in graph:
            visit(task_name)

        courses.append(Course(
            name=name, exam_day=exam_day, credits=credits, degree=degree,
            priority=priority, n_diag=n_diag, a=a, b=b, min_hours=min_hours,
            knowledge=knowledge, tasks=tasks,
        ))
    return courses


def _parse_daily_hours(raw: Any) -> dict[int, float]:
    if isinstance(raw, list):
        items = enumerate(raw)
    elif isinstance(raw, dict):
        items = raw.items()
    else:
        raise SchedulerInputError("exam_plan.daily_hours 必须是对象或数组")

    result: dict[int, float] = {}
    for raw_day, raw_hours in items:
        day = _integer(raw_day, "daily_hours 的日期键", minimum=0)
        if day in result:
            raise SchedulerInputError(f"daily_hours 日期重复：{day}")
        result[day] = _number(raw_hours, f"daily_hours[{day}]", minimum=0.0)
    if not result:
        raise SchedulerInputError("exam_plan.daily_hours 不能为空")
    return result


def _risk_view(courses: list[Course], base_day: int) -> list[Course]:
    """构造风险评分视图：exam_day 换算为相对 base_day 的剩余天数。

    allocate 始终使用绝对日序号；risk_scores 的 D = urgency(剩余天数)。
    视图副本与原件共享 tasks 等可变对象，但 risk_scores 只读取
    exam_day / credits / degree / priority / n_diag / knowledge，
    不会触碰任务状态，因此共享是安全的。
    """
    return [
        Course(
            name=course.name,
            exam_day=max(0, course.exam_day - base_day),
            credits=course.credits,
            degree=course.degree,
            priority=course.priority,
            n_diag=course.n_diag,
            a=course.a,
            b=course.b,
            min_hours=course.min_hours,
            knowledge=course.knowledge,
            tasks=course.tasks,
        )
        for course in courses
    ]


def _schedule_rows(schedule: dict, courses: list[Course]) -> list[dict]:
    task_lookup = {
        (course.name, task.name): task
        for course in courses for task in course.tasks
    }
    rows = []
    for day in sorted(schedule):
        for course_name, task_name, hours in schedule[day]:
            task = task_lookup[(course_name, task_name)]
            rows.append({
                "day": int(day), "course": course_name, "task": task_name,
                "knowledge_point": task.kp, "required": task.required,
                "hours": round(float(hours), 6),
            })
    return rows


def _diff_rows(v1_days: dict, v2_days: dict) -> list[dict]:
    rows = []
    days = sorted(set(v1_days) | set(v2_days))
    names = sorted({
        name for day in days
        for name in set(v1_days.get(day, {})) | set(v2_days.get(day, {}))
    })
    for day in days:
        for name in names:
            before = float(v1_days.get(day, {}).get(name, 0.0))
            after = float(v2_days.get(day, {}).get(name, 0.0))
            delta = after - before
            if abs(delta) > 1e-9:
                rows.append({
                    "day": int(day), "course": name,
                    "v1_hours": round(before, 3),
                    "v2_hours": round(after, 3),
                    "delta_hours": round(delta, 3),
                })
    return rows


def _render_risks(risks: dict[str, dict]) -> list[str]:
    lines = [
        "| 课程 | D 紧迫度 | M 掌握缺口 | W 重要度 | U 不确定性 | R 综合风险 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, values in risks.items():
        lines.append(
            f"| {name} | {values['D']:.3f} | {values['M']:.3f} | "
            f"{values['W']:.3f} | {values['U']:.3f} | {values['R']:.3f} |"
        )
    return lines


def _render_schedule(title: str, schedule: dict, courses: list[Course],
                     allocated: dict[str, float]) -> list[str]:
    lines = [f"#### {title}"]
    rows = _schedule_rows(schedule, courses)
    if rows:
        lines.extend([
            "| 日期序号 | 课程 | 任务 | 知识点 | 类型 | 分配时间 |",
            "| ---: | --- | --- | --- | --- | ---: |",
        ])
        for row in rows:
            kind = "必修" if row["required"] else "可选"
            lines.append(
                f"| Day {row['day']} | {row['course']} | {row['task']} | "
                f"{row['knowledge_point']} | {kind} | {row['hours']:.2f}h |"
            )
    else:
        lines.append("当前时间窗口内没有可分配任务。")
    if allocated:
        totals = "；".join(f"{name} {hours:.2f}h" for name, hours in allocated.items())
        lines.append(f"\n课程分配合计：{totals}")
    return lines


def _render_warnings(warnings: list[str]) -> list[str]:
    if not warnings:
        return ["无未解决的排程预警。"]
    return [f"- {warning}" for warning in warnings]


def run_exam_plan(spec: Any) -> dict:
    """运行初始排程；附带 execution_feedback 时再执行一次 v1→v2 重规划。"""
    if not isinstance(spec, dict):
        raise SchedulerInputError("exam_plan 顶层必须是 JSON 对象")

    courses = _parse_courses(spec.get("courses"))
    daily_hours = _parse_daily_hours(spec.get("daily_hours"))
    start_day = _integer(spec.get("start_day", 0), "exam_plan.start_day", minimum=0)

    raw_weights = spec.get("risk_weights", [0.25, 0.25, 0.25, 0.25])
    if not isinstance(raw_weights, list) or len(raw_weights) != 4:
        raise SchedulerInputError("exam_plan.risk_weights 必须是长度为 4 的数组")
    weights = tuple(
        _number(value, f"risk_weights[{index}]", minimum=0.0)
        for index, value in enumerate(raw_weights)
    )
    if sum(weights) <= 0:
        raise SchedulerInputError("risk_weights 至少有一个权重必须大于 0")

    # D 因子使用"距考试剩余天数"视图；allocate 使用绝对日序号。
    risks_v1 = risk_scores(_risk_view(courses, start_day), weights=weights)
    schedule_v1, allocated_v1, warnings_v1, _ = allocate(
        courses, daily_hours, risks_v1, start_day=start_day
    )
    initial_v1_rows = _schedule_rows(schedule_v1, courses)
    feedback = spec.get("execution_feedback")

    feedback_detail: list[dict] = []
    diff_rows: list[dict] = []
    final_schedule = schedule_v1
    final_allocated = allocated_v1
    final_warnings = warnings_v1
    final_risks = risks_v1
    final_bad = conservation_check(courses, schedule_v1)
    mode = "v1"

    report_lines = [
        "### ExamOS 确定性排程结果",
        "#### 5.1 课程风险评分",
        *_render_risks(risks_v1),
    ]

    if feedback is not None:
        if not isinstance(feedback, dict):
            raise SchedulerInputError("exam_plan.execution_feedback 必须是 JSON 对象")
        feedback_day = _integer(
            feedback.get("day", start_day), "execution_feedback.day", minimum=0
        )
        if feedback_day != start_day:
            raise SchedulerInputError(
                "单次重规划的 execution_feedback.day 必须等于 start_day；"
                "请以当前执行日作为本轮规划起点"
            )
        if feedback_day >= max(course.exam_day for course in courses):
            raise SchedulerInputError("execution_feedback.day 不能晚于所有课程的考试日")

        completion = feedback.get("completion", {})
        if not isinstance(completion, dict):
            raise SchedulerInputError("execution_feedback.completion 必须是对象")
        unknown_courses = set(completion) - {course.name for course in courses}
        if unknown_courses:
            raise SchedulerInputError(
                "execution_feedback.completion 包含未知课程："
                + ", ".join(sorted(unknown_courses))
            )
        for name, rate in completion.items():
            _number(rate, f"completion[{name}]", minimum=0.0, maximum=1.0)
        default_rate = _number(
            feedback.get("default_rate", 1.0),
            "execution_feedback.default_rate",
            minimum=0.0,
            maximum=1.0,
        )

        v1_schedule_snapshot = {day: list(blocks) for day, blocks in schedule_v1.items()}
        v1_day_snapshot = hours_by_day(schedule_v1)
        detail = apply_day_completion(
            courses, schedule_v1, feedback_day,
            completion=completion, default_rate=default_rate,
        )
        for course_name, task_name, planned, done, returned in detail:
            feedback_detail.append({
                "course": course_name, "task": task_name,
                "planned_hours": round(planned, 6),
                "completed_hours": round(done, 6),
                "returned_hours": round(returned, 6),
            })

        next_day = feedback_day + 1
        rollback_future(courses, schedule_v1, start_day=next_day)
        feedback_bad = conservation_check(courses, schedule_v1)

        # 不原地改写 course.exam_day：allocate 需要绝对日序号，
        # 仅 risk_scores 的 D 因子换算为相对 next_day 的剩余天数视图；
        # 掌握缺口 M 不因单纯未完成计划而变化。
        final_risks = risk_scores(_risk_view(courses, next_day), weights=weights)
        final_schedule, final_allocated, final_warnings, _ = allocate(
            courses, daily_hours, final_risks, start_day=next_day
        )
        final_bad = conservation_check(courses, final_schedule)
        if feedback_bad:
            final_bad = sorted(set(feedback_bad + final_bad))
        diff_rows = _diff_rows(
            {day: values for day, values in v1_day_snapshot.items() if day >= next_day},
            hours_by_day(final_schedule),
        )
        mode = "v2"

        report_lines.extend([
            "",
            *_render_schedule(
                "v1 初始计划（执行反馈前）",
                v1_schedule_snapshot, courses, allocated_v1
            ),
            "",
            "#### 执行反馈",
        ])
        if feedback_detail:
            report_lines.extend([
                "| 课程 | 任务 | 原计划 | 完成 | 回补 |",
                "| --- | --- | ---: | ---: | ---: |",
            ])
            for row in feedback_detail:
                report_lines.append(
                    f"| {row['course']} | {row['task']} | {row['planned_hours']:.2f}h | "
                    f"{row['completed_hours']:.2f}h | {row['returned_hours']:.2f}h |"
                )
        else:
            report_lines.append("反馈日没有已排入的任务块。")

        report_lines.extend([
            "",
            "#### 6.3 重规划后的风险评分",
            *_render_risks(final_risks),
            "",
            *_render_schedule(
                "v2 动态重规划（剩余时间）",
                final_schedule, courses, final_allocated
            ),
            "",
            "#### v1 / v2 分配差异报告",
        ])
        if diff_rows:
            report_lines.extend([
                "| 日期序号 | 课程 | v1 | v2 | Δ（v2-v1） |",
                "| ---: | --- | ---: | ---: | ---: |",
            ])
            for row in diff_rows:
                report_lines.append(
                    f"| Day {row['day']} | {row['course']} | "
                    f"{row['v1_hours']:.2f}h | {row['v2_hours']:.2f}h | "
                    f"{row['delta_hours']:+.2f}h |"
                )
        else:
            report_lines.append("本轮重规划未改变未来课程分配。")
        report_lines.append(
            "掌握缺口 M 保持不变，除非另有新的测验证据更新知识点掌握度。"
        )
    else:
        report_lines.extend([
            "",
            *_render_schedule("v1 初始计划", final_schedule, courses, final_allocated),
        ])

    report_lines.extend([
        "",
        "#### 时间不足预警与剩余风险",
        *_render_warnings(final_warnings),
    ])
    report_lines.append(
        f"\n守恒校验：{'通过' if not final_bad else '失败：' + str(final_bad)}"
    )

    task_state = []
    for course in courses:
        for task in course.tasks:
            task_state.append({
                "course": course.name, "task": task.name,
                "required": task.required,
                "total_hours": round(task.hours, 6),
                "done_hours": round(task.done, 6),
                "remaining_hours": round(task.remaining, 6),
            })

    data = {
        "mode": mode,
        "risk_scores_initial": risks_v1,
        "risk_scores_final": final_risks,
        "schedule": {
            str(day): [
                {"course": cname, "task": tname, "hours": round(hours, 6)}
                for cname, tname, hours in blocks
            ]
            for day, blocks in final_schedule.items()
        },
        "schedule_v1": initial_v1_rows,
        "schedule_final": _schedule_rows(final_schedule, courses),
        "allocated_hours": {
            name: round(hours, 6) for name, hours in final_allocated.items()
        },
        "warnings": list(final_warnings),
        "feedback": feedback_detail,
        "allocation_diff": diff_rows,
        "task_state": task_state,
        "conservation_ok": not final_bad,
        "conservation_issues": [list(item) for item in final_bad],
    }
    return {"report": "\n".join(report_lines), "data": data}
