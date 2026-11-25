# -*- coding: utf-8 -*-
"""进度监控 Agent：执行偏差检测与重规划触发判定（确定性，无 LLM 决策）。

对应设计书 6.2/6.3：

- 候选触发信号（任一满足）：
  T1 单日完成率偏差 > 显著性阈值（默认 0.4），或连续两日完成率 < 60%；
  T2 新测验证据使某课程掌握度估计显著变化（外部信号，由画像层给出）；
  T3 考试安排 / 可用时间等输入条件变化（外部信号）；
  T4 距考试不足 N 天且必修任务未完成比例超过安全线；
- 两道闸门：显著性阈值 + 冷却时间（同一课程重规划后 24 小时内
  不因同类信号重复触发，学生手动请求除外）。

输出：
- triggered / triggers / suppressed：触发判定与证据；
- execution_feedback：触发时为 scheduler_adapter.run_exam_plan
  的 execution_feedback 段（day / completion / default_rate），
  未触发或被冷却拦下时为 None；
- output：面向学生的偏差报告文本。

本 Agent 只做"何时重算计划"的判定；掌握度 M_i 不由完成率修改
（没复习不等于更不会），计划解释由规划 Agent 的 LLM 层负责。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base_agent import BaseAgent


class ProgressMonitorError(ValueError):
    """进度监控输入不合法。"""


DEFAULT_THRESHOLDS = {
    "daily_deviation": 0.4,      # T1 单日完成率偏差阈值
    "consecutive_rate": 0.6,     # T1 连续两日完成率下限
    "safety_days": 2,            # T4 距考试天数安全线
    "safety_unfinished": 0.5,    # T4 必修未完成比例安全线
    "cooldown_hours": 24.0,      # 冷却时间
}


def _rate(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ProgressMonitorError(f"{field} 必须是数字，不能是布尔值")
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise ProgressMonitorError(f"{field} 必须是数字") from None
    if not 0.0 <= rate <= 1.0:
        raise ProgressMonitorError(f"{field} 必须落在 [0, 1]：{value!r}")
    return rate


def _parse_time(value: Any, field: str) -> datetime:
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ProgressMonitorError(
            f"{field} 必须是 ISO 时间格式：{value!r}"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class ProgressMonitorAgent(BaseAgent):
    """进度监控 Agent：完成率 → 触发判定 → execution_feedback。"""

    name = "进度监控Agent"

    def run(self, context: dict) -> dict:
        completion_raw = context.get("completion")
        if not isinstance(completion_raw, dict) or not completion_raw:
            raise ProgressMonitorError("context.completion 必须是非空对象（课程→完成率）")
        completion = {
            str(name): _rate(rate, f"completion[{name!r}]")
            for name, rate in completion_raw.items()
        }

        day = context.get("day", 0)
        if not isinstance(day, int) or isinstance(day, bool) or day < 0:
            raise ProgressMonitorError("context.day 必须是非负整数")

        default_rate = _rate(context.get("default_rate", 1.0), "default_rate")

        thresholds = dict(DEFAULT_THRESHOLDS)
        for key, value in (context.get("thresholds") or {}).items():
            if key not in thresholds:
                raise ProgressMonitorError(f"未知阈值参数：{key}")
            thresholds[key] = float(value)

        signals = context.get("signals") or {}
        if not isinstance(signals, dict):
            raise ProgressMonitorError("context.signals 必须是对象")
        mastery_changed = {str(n) for n in (signals.get("mastery_changed") or [])}
        constraints_changed = bool(signals.get("constraints_changed", False))
        manual_request = bool(signals.get("manual_request", False))

        now = _parse_time(context.get("now"), "now") if context.get("now") \
            else datetime.now(timezone.utc)
        history = context.get("history") or {}
        last_replan = history.get("last_replan", {}) or {}
        recent_completion = history.get("recent_completion", {}) or {}

        # 当日各课程计划时长（用于偏差报告的剩余工作量估计）
        planned_today: dict[str, float] = {}
        schedule = (context.get("plan") or {}).get("schedule") or {}
        for block in schedule.get(str(day), []) or []:
            course = str(block.get("course", ""))
            planned_today[course] = planned_today.get(course, 0.0) + float(
                block.get("hours", 0.0)
            )

        course_status = context.get("course_status") or {}

        triggers: list[dict] = []
        suppressed: list[dict] = []
        lines: list[str] = [f"【进度监控】Day {day} 执行反馈检查"]

        for course, rate in sorted(completion.items()):
            reasons: list[tuple[str, str]] = []  # (信号类型, 说明)

            deviation = 1.0 - rate
            if deviation > thresholds["daily_deviation"]:
                reasons.append((
                    "completion",
                    f"单日完成率 {rate:.0%}，偏差 {deviation:.0%} 超过阈值 "
                    f"{thresholds['daily_deviation']:.0%}",
                ))
            else:
                history_rates = [
                    _rate(v, f"recent_completion[{course!r}]")
                    for v in (recent_completion.get(course) or [])
                ]
                streak = history_rates[-1:] + [rate]
                if (len(streak) == 2 and all(
                        r < thresholds["consecutive_rate"] for r in streak)):
                    reasons.append((
                        "completion",
                        f"连续两日完成率低于 {thresholds['consecutive_rate']:.0%}",
                    ))

            if course in mastery_changed:
                reasons.append(("mastery", "新测验证据使掌握度估计显著变化"))
            if constraints_changed:
                reasons.append(("constraints", "考试安排或可用时间等输入条件变化"))

            status = course_status.get(course) or {}
            days_left = status.get("days_to_exam")
            unfinished = status.get("required_unfinished_ratio")
            if days_left is not None and unfinished is not None:
                if (int(days_left) <= thresholds["safety_days"]
                        and float(unfinished) > thresholds["safety_unfinished"]):
                    reasons.append((
                        "safety",
                        f"距考试 {int(days_left)} 天且必修任务未完成比例 "
                        f"{float(unfinished):.0%} 超过安全线 "
                        f"{thresholds['safety_unfinished']:.0%}",
                    ))

            planned = planned_today.get(course, 0.0)
            backlog = planned * (1.0 - rate)
            lines.append(
                f"· {course}：完成率 {rate:.0%}"
                + (f"，剩余工作量回补 {backlog:.1f}h（原计划 {planned:.1f}h）"
                   if planned else "")
            )

            if not reasons and not manual_request:
                continue

            if manual_request and not reasons:
                reasons.append(("manual", "学生手动请求重规划"))

            # 冷却闸门：仅拦截完成率类（同类）信号的重复触发；
            # 新证据、约束变化、安全线、手动请求不受冷却限制
            kinds = {kind for kind, _ in reasons}
            if (not manual_request and kinds == {"completion"}
                    and course in last_replan):
                last = _parse_time(last_replan[course],
                                   f"last_replan[{course!r}]")
                elapsed_h = (now - last).total_seconds() / 3600.0
                if elapsed_h < thresholds["cooldown_hours"]:
                    suppressed.append({
                        "course": course,
                        "reasons": [text for _, text in reasons],
                        "cooldown_remaining_h": round(
                            thresholds["cooldown_hours"] - elapsed_h, 2),
                    })
                    lines.append(
                        f"  ↳ {course} 满足完成率类触发条件，但距上次重规划 "
                        f"{elapsed_h:.1f}h，仍在冷却期内（剩余 "
                        f"{thresholds['cooldown_hours'] - elapsed_h:.1f}h），"
                        "本次不重复触发"
                    )
                    continue

            triggers.append({
                "course": course,
                "kinds": sorted(kinds),
                "reasons": [text for _, text in reasons],
            })

        triggered = bool(triggers)
        if triggered:
            names = "、".join(t["course"] for t in triggers)
            lines.append(f"结论：检测到显著偏差，触发重规划（{names}）。")
        else:
            lines.append("结论：无显著偏差，继续执行当前计划。")
        lines.append(
            "注：掌握度 M 不因未完成计划而变化（没复习不等于更不会）；"
            "仅当有新测验证据时由画像层更新。"
        )

        return {
            "output": "\n".join(lines),
            "triggered": triggered,
            "triggers": triggers,
            "suppressed": suppressed,
            "execution_feedback": (
                {"day": day, "completion": completion,
                 "default_rate": default_rate}
                if triggered else None
            ),
            "thresholds": thresholds,
        }
