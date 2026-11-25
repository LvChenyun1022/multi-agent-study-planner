# -*- coding: utf-8 -*-
"""ExamOS 前端状态层：JSON 持久化 + 多日推进 + 重规划编排。

坐标系契约（与 agents/scheduler_adapter.py 一致）：
- exam_day 为相对规划原点 day 0 的**绝对日序号**（origin_date 即 day 0）；
- current_day 为当前执行日（绝对日序号）；
- daily_hours 的键为绝对日序号。

多日推进的两条不变量：

1. 任务总量守恒：total = done + remaining + 当前计划已分配
   （排程引擎 conservation_check 保证，本层每次状态变更后复核）；
2. 状态推进只经两条路径——
   a) 触发重规划：直接编排 scheduler 原语（apply_day_completion →
      rollback_future → allocate），执行反馈作用在**学生当天实际看到的**
      计划块上，而不是重新生成的 v1 上；
   b) 未触发：仅按当日计划块做 done/remaining 簿记并移除已执行日，
      未来计划保持不变（显著性闸门的意义所在）。

掌握度纪律：掌握度 M 只由测验证据更新；本层不接受"未完成 → 降掌握度"。
前端滑杆写入的是自评值，并保留来源标注（中性先验 / 自评 / 诊断证据）。
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any

from scheduler import (
    Course, Task, allocate, apply_day_completion, conservation_check,
    hours_by_day, risk_scores, rollback_future,
)
from agents.progress_monitor_agent import ProgressMonitorAgent
from agents.scheduler_adapter import run_exam_plan

STATE_VERSION = 1
STATE_PATH = "examos_data.json"
DEFAULT_DAILY_HOURS = 6.0


class StateError(ValueError):
    """前端状态不合法或当前操作没有可行的计划上下文。"""


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def default_state() -> dict:
    return {
        "version": STATE_VERSION,
        "origin_date": date.today().isoformat(),
        "current_day": 0,
        "courses": [],
        "daily_hours": {},
        "plan_versions": [],
        "current_plan": None,          # plan_versions 的索引
        "feedback_log": [],
        "last_replan": {},             # 课程名 -> ISO 时间
        "recent_completion": {},       # 课程名 -> [近期完成率]
        "mastery_changed_pending": [],  # 画像页修改掌握度后待监控消费的课程
    }


def load_state(path: str = STATE_PATH) -> dict:
    if not os.path.exists(path):
        return default_state()
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise StateError(f"状态文件 {path} 读取失败：{exc}") from None
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        raise StateError(f"状态文件 {path} 版本不兼容，请备份后删除重建")
    for key, value in default_state().items():
        state.setdefault(key, value)
    return state


def save_state(state: dict, path: str = STATE_PATH) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 基础查询
# ---------------------------------------------------------------------------
def day_to_date(state: dict, day: int) -> str:
    origin = date.fromisoformat(state["origin_date"])
    return date.fromordinal(origin.toordinal() + day).isoformat()


def find_course(state: dict, name: str) -> dict | None:
    return next((c for c in state["courses"] if c["name"] == name), None)


def task_remaining(course: dict, task_name: str) -> float:
    ts = (course.get("task_state") or {}).get(task_name)
    if ts is None:
        task = next(t for t in course["tasks"] if t["name"] == task_name)
        return float(task["hours"])
    return float(ts["remaining"])


def current_plan(state: dict) -> dict | None:
    index = state.get("current_plan")
    if index is None:
        return None
    versions = state.get("plan_versions") or []
    if not (0 <= index < len(versions)):
        return None
    return versions[index]


def plan_blocks(plan: dict | None, day: int) -> list[dict]:
    if not plan:
        return []
    return list((plan.get("schedule") or {}).get(str(day), []) or [])


def max_exam_day(state: dict) -> int:
    days = [int(c["exam_day"]) for c in state["courses"]]
    return max(days) if days else 0


def active_courses(state: dict, day: int) -> list[dict]:
    """考试日之后（含当天）的课程不再参与排程。"""
    return [c for c in state["courses"] if int(c["exam_day"]) > day]


def course_status(state: dict, day: int) -> dict:
    """ProgressMonitor T4 安全线所需的课程状态。"""
    status = {}
    for course in active_courses(state, day):
        required = [t for t in course["tasks"] if t["required"]]
        total = sum(float(t["hours"]) for t in required)
        remaining = sum(task_remaining(course, t["name"]) for t in required)
        status[course["name"]] = {
            "days_to_exam": int(course["exam_day"]) - day,
            "required_unfinished_ratio": (remaining / total) if total > 0 else 0.0,
        }
    return status


# ---------------------------------------------------------------------------
# scheduler 对象重建与风险视图
# ---------------------------------------------------------------------------
def reconstruct_courses(state: dict, day: int) -> list[Course]:
    """把持久化课程档案重建为 scheduler 对象（exam_day 保持绝对日序号）。"""
    courses: list[Course] = []
    for record in active_courses(state, day):
        ts_map = record.get("task_state") or {}
        tasks: list[Task] = []
        for t in record["tasks"]:
            task = Task(t["name"], t["kp"], float(t["hours"]),
                        bool(t["required"]), deps=list(t.get("deps", [])))
            saved = ts_map.get(t["name"])
            if saved:
                task.done = float(saved.get("done", 0.0))
                task.remaining = float(saved.get("remaining", t["hours"]))
            tasks.append(task)
        courses.append(Course(
            name=record["name"],
            exam_day=int(record["exam_day"]),
            credits=float(record["credits"]),
            degree=bool(record["degree"]),
            priority=float(record["priority"]),
            n_diag=int(record["n_diag"]),
            a=float(record["a"]),
            b=float(record["b"]),
            min_hours=float(record["min_hours"]),
            knowledge=[(k["name"], float(k["weight"]), float(k["mastery"]))
                       for k in record["knowledge"]],
            tasks=tasks,
        ))
    return courses


def _risk_view(courses: list[Course], base_day: int) -> list[Course]:
    """D 因子需要剩余天数；allocate 需要绝对日序号。视图仅供 risk_scores。"""
    return [replace(c, exam_day=max(0, c.exam_day - base_day)) for c in courses]


def _to_tuple_schedule(schedule: dict) -> dict[int, list[tuple]]:
    """持久化的 dict 计划块 → scheduler 原语使用的 tuple 计划。"""
    return {
        int(day): [(b["course"], b["task"], float(b["hours"])) for b in blocks]
        for day, blocks in schedule.items()
    }


def _to_json_schedule(schedule: dict) -> dict[str, list[dict]]:
    return {
        str(day): [
            {"course": cname, "task": tname, "hours": round(float(h), 6)}
            for cname, tname, h in blocks
        ]
        for day, blocks in sorted(schedule.items())
    }


def _persist_task_state(state: dict, courses: list[Course]) -> None:
    for course in courses:
        record = find_course(state, course.name)
        if record is None:
            continue
        record["task_state"] = {
            t.name: {"done": round(t.done, 6), "remaining": round(t.remaining, 6)}
            for t in course.tasks
        }


def _check_conservation(state: dict, courses: list[Course]) -> list:
    plan = current_plan(state)
    schedule = _to_tuple_schedule((plan or {}).get("schedule") or {})
    return conservation_check(courses, schedule)


# ---------------------------------------------------------------------------
# 初始规划（走经过完整校验的 run_exam_plan）
# ---------------------------------------------------------------------------
def build_exam_plan_spec(state: dict, start_day: int) -> dict:
    """构造 run_exam_plan 的标准输入；任务工时为当前剩余工作量。"""
    courses = []
    for record in active_courses(state, start_day):
        tasks = [
            {
                "name": t["name"], "kp": t["kp"],
                "hours": round(task_remaining(record, t["name"]), 6),
                "required": bool(t["required"]),
                "deps": list(t.get("deps", [])),
            }
            for t in record["tasks"]
        ]
        courses.append({
            "name": record["name"],
            "exam_day": int(record["exam_day"]),
            "credits": float(record["credits"]),
            "degree": bool(record["degree"]),
            "priority": float(record["priority"]),
            "n_diag": int(record["n_diag"]),
            "a": float(record["a"]),
            "b": float(record["b"]),
            "min_hours": float(record["min_hours"]),
            "knowledge": [
                {"name": k["name"], "weight": float(k["weight"]),
                 "mastery": float(k["mastery"])}
                for k in record["knowledge"]
            ],
            "tasks": tasks,
        })
    if not courses:
        raise StateError("没有考试日尚未到来的课程，无法生成复习计划")
    daily = {str(d): float(h) for d, h in state["daily_hours"].items()
             if int(d) >= start_day and float(h) > 0}
    if not daily:
        raise StateError("请先在「复习计划」页设置每日可用时间")
    return {"courses": courses, "daily_hours": daily, "start_day": int(start_day)}


def _new_version(state: dict, *, start_day: int, kind: str, mode: str,
                 schedule: dict, risks: dict, warnings: list,
                 feedback: list | None = None,
                 allocation_diff: list | None = None,
                 conservation_ok: bool) -> dict:
    version = {
        "version": len(state["plan_versions"]) + 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "start_day": int(start_day),
        "kind": kind,
        "mode": mode,
        "schedule": schedule,
        "risks": risks,
        "warnings": list(warnings),
        "feedback": feedback or [],
        "allocation_diff": allocation_diff or [],
        "conservation_ok": bool(conservation_ok),
    }
    state["plan_versions"].append(version)
    state["current_plan"] = len(state["plan_versions"]) - 1
    return version


def initial_plan(state: dict, start_day: int, *, kind: str = "初始规划") -> dict:
    """首次（或推倒重来）生成计划：run_exam_plan 校验 + v1 求解。"""
    spec = build_exam_plan_spec(state, start_day)
    result = run_exam_plan(spec)
    data = result["data"]
    version = _new_version(
        state, start_day=start_day, kind=kind, mode="v1",
        schedule=data["schedule"],
        risks=data["risk_scores_final"],
        warnings=data["warnings"],
        conservation_ok=data["conservation_ok"],
    )
    # run_exam_plan 以剩余工时为输入重新排程：持久化 remaining 采用运行结果
    for row in data["task_state"]:
        record = find_course(state, row["course"])
        if record is None:
            continue
        ts = record.setdefault("task_state", {})
        previous = ts.get(row["task"], {})
        ts[row["task"]] = {
            "done": float(previous.get("done", 0.0)) + float(row["done_hours"]),
            "remaining": float(row["remaining_hours"]),
        }
    return version


# ---------------------------------------------------------------------------
# 执行反馈：监控判定 → （触发）重规划 / （未触发）簿记
# ---------------------------------------------------------------------------
def checkin(state: dict, day: int, completion: dict[str, float], *,
            default_rate: float = 1.0, manual: bool = False,
            now: datetime | None = None) -> dict:
    """提交某日执行反馈。返回监控结果与（可能的）新计划版本。"""
    now = now or datetime.now(timezone.utc)
    plan = current_plan(state)
    if plan is None:
        raise StateError("当前没有生效的复习计划，请先在「复习计划」页生成")
    if not active_courses(state, day):
        raise StateError("所有课程的考试日均已过去，无需再提交执行反馈")
    completion = {str(k): float(v) for k, v in completion.items()}

    mastery_pending = list(state.get("mastery_changed_pending") or [])
    monitor = ProgressMonitorAgent(None, None, None)
    monitor_result = monitor.run({
        "completion": completion,
        "day": int(day),
        "default_rate": float(default_rate),
        "plan": {"schedule": plan.get("schedule") or {}},
        "course_status": course_status(state, day),
        "signals": {
            "mastery_changed": mastery_pending,
            "constraints_changed": False,
            "manual_request": bool(manual),
        },
        "history": {
            "last_replan": state.get("last_replan") or {},
            "recent_completion": state.get("recent_completion") or {},
        },
        "now": now.isoformat(),
    })
    state["mastery_changed_pending"] = []

    for name, rate in completion.items():
        history = state["recent_completion"].setdefault(name, [])
        history.append(rate)
        del history[:-7]

    outcome: dict[str, Any] = {
        "monitor": monitor_result,
        "replanned": False,
        "version": None,
    }

    if monitor_result["triggered"]:
        kind = "手动重规划" if manual and not any(
            k != "manual" for t in monitor_result["triggers"] for k in t["kinds"]
        ) else "显著偏差重规划"
        version = _replan_with_feedback(
            state, day, completion, default_rate=default_rate, kind=kind,
        )
        for trigger in monitor_result["triggers"]:
            state["last_replan"][trigger["course"]] = now.isoformat()
        outcome["replanned"] = True
        outcome["version"] = version
    else:
        _bookkeep_without_replan(state, day, completion, default_rate)

    state["feedback_log"].append({
        "day": int(day),
        "at": now.isoformat(),
        "completion": completion,
        "triggered": monitor_result["triggered"],
        "triggers": monitor_result["triggers"],
        "suppressed": monitor_result["suppressed"],
        "replanned": outcome["replanned"],
    })
    return outcome


def _bookkeep_without_replan(state: dict, day: int,
                             completion: dict[str, float],
                             default_rate: float) -> None:
    """未触发路径：只做 done/remaining 簿记并移除已执行日，未来计划不变。"""
    plan = current_plan(state)
    schedule = plan.get("schedule") or {}
    blocks = schedule.pop(str(day), [])
    for block in blocks:
        record = find_course(state, block["course"])
        if record is None:
            continue
        ts = record.setdefault("task_state", {}).setdefault(
            block["task"],
            {"done": 0.0,
             "remaining": task_remaining(record, block["task"])},
        )
        rate = float(completion.get(block["course"], default_rate))
        hours = float(block["hours"])
        ts["done"] = round(float(ts["done"]) + hours * rate, 6)
        ts["remaining"] = round(float(ts["remaining"]) + hours * (1.0 - rate), 6)
    bad = _check_conservation(state, reconstruct_courses(state, day + 1))
    if bad:
        raise StateError(f"簿记后守恒校验失败：{bad}")


def _replan_with_feedback(state: dict, day: int, completion: dict[str, float],
                          *, default_rate: float, kind: str) -> dict:
    """触发路径：反馈作用在当天实际执行的计划块上，退回未来块后重新求解。"""
    courses = reconstruct_courses(state, day)
    plan = current_plan(state)
    old_schedule = _to_tuple_schedule(plan.get("schedule") or {})
    old_future = {d: blocks for d, blocks in old_schedule.items() if d > day}
    old_day_snapshot = hours_by_day({d: b for d, b in old_future.items()})

    working = {d: list(blocks) for d, blocks in old_schedule.items() if d >= day}
    detail = apply_day_completion(
        courses, working, day, completion=completion, default_rate=default_rate,
    )
    rollback_future(courses, working, start_day=day + 1)

    daily = {int(d): float(h) for d, h in state["daily_hours"].items()}
    risks = risk_scores(_risk_view(courses, day + 1))
    new_schedule, _, warnings, _ = allocate(
        courses, daily, risks, start_day=day + 1,
    )
    bad = conservation_check(courses, new_schedule)
    _persist_task_state(state, courses)

    feedback_rows = [
        {"course": c, "task": t, "planned_hours": round(p, 6),
         "completed_hours": round(dn, 6), "returned_hours": round(rt, 6)}
        for c, t, p, dn, rt in detail
    ]
    diff_rows = _allocation_diff(old_day_snapshot, hours_by_day(new_schedule))
    return _new_version(
        state, start_day=day + 1, kind=kind, mode="v2",
        schedule=_to_json_schedule(new_schedule),
        risks=risks, warnings=warnings, feedback=feedback_rows,
        allocation_diff=diff_rows, conservation_ok=not bad,
    )


def _allocation_diff(old_days: dict, new_days: dict) -> list[dict]:
    rows = []
    for day in sorted(set(old_days) | set(new_days)):
        for name in sorted(set(old_days.get(day, {})) | set(new_days.get(day, {}))):
            before = float(old_days.get(day, {}).get(name, 0.0))
            after = float(new_days.get(day, {}).get(name, 0.0))
            if abs(after - before) > 1e-9:
                rows.append({"day": int(day), "course": name,
                             "v1_hours": round(before, 3),
                             "v2_hours": round(after, 3),
                             "delta_hours": round(after - before, 3)})
    return rows


# ---------------------------------------------------------------------------
# 手动 / 约束变化重规划（无执行反馈）：退回未来块，按最新输入重排
# ---------------------------------------------------------------------------
def replan(state: dict, start_day: int, *, kind: str) -> dict:
    if not state["courses"]:
        raise StateError("尚未登记任何课程")
    courses = reconstruct_courses(state, start_day)
    if not courses:
        raise StateError("没有考试日尚未到来的课程，无法重排")
    plan = current_plan(state)
    if plan:
        old_schedule = _to_tuple_schedule(plan.get("schedule") or {})
        future = {d: blocks for d, blocks in old_schedule.items() if d >= start_day}
        if future:
            rollback_future(courses, future, start_day=start_day)
    daily = {int(d): float(h) for d, h in state["daily_hours"].items()}
    risks = risk_scores(_risk_view(courses, start_day))
    new_schedule, _, warnings, _ = allocate(
        courses, daily, risks, start_day=start_day,
    )
    bad = conservation_check(courses, new_schedule)
    _persist_task_state(state, courses)
    return _new_version(
        state, start_day=start_day, kind=kind, mode="v2",
        schedule=_to_json_schedule(new_schedule),
        risks=risks, warnings=warnings, conservation_ok=not bad,
    )


# ---------------------------------------------------------------------------
# 课程档案维护
# ---------------------------------------------------------------------------
def upsert_course(state: dict, record: dict) -> None:
    """新增或覆盖课程档案；已有任务的 done/remaining 状态按任务名保留。"""
    existing = find_course(state, record["name"])
    if existing is not None:
        old_ts = existing.get("task_state") or {}
        record["task_state"] = {
            name: ts for name, ts in old_ts.items()
            if name in {t["name"] for t in record["tasks"]}
        }
        state["courses"] = [
            record if c["name"] == record["name"] else c for c in state["courses"]
        ]
    else:
        record.setdefault("task_state", {})
        state["courses"].append(record)


def remove_course(state: dict, name: str) -> None:
    state["courses"] = [c for c in state["courses"] if c["name"] != name]
    state["last_replan"].pop(name, None)
    state["recent_completion"].pop(name, None)
    # 同步清理各版本计划中的该课程计划块，避免后续重规划查找失败
    for version in state.get("plan_versions") or []:
        schedule = version.get("schedule") or {}
        for day in list(schedule):
            blocks = [b for b in schedule[day] if b.get("course") != name]
            if blocks:
                schedule[day] = blocks
            else:
                del schedule[day]
