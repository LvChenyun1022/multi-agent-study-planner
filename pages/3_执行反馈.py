# -*- coding: utf-8 -*-
"""页面 3：执行反馈 —— 按日打卡 → 进度监控 → 显著偏差触发重规划。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import examos_state as es
import examos_ui as ui

ui.init_page("执行反馈", "✅")
state = ui.get_state()
ui.sidebar_status(state)

st.markdown('<div class="main-header">✅ 执行反馈</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">进度监控 Agent 判定偏差显著性；'
    '触发时退回未来计划块并重新求解，未触发时仅记录完成进度。'
    '掌握度不因未完成计划而变化（没复习不等于更不会）。</div>',
    unsafe_allow_html=True,
)

today = int(state["current_day"])
plan = es.current_plan(state)

if plan is None:
    st.info("尚未生成复习计划。请先在「复习计划」页生成。")
    st.stop()
if not es.active_courses(state, today):
    st.success("所有课程的考试均已结束，无需再提交执行反馈。")
    st.stop()

blocks_today = es.plan_blocks(plan, today)
already_checked = any(log["day"] == today for log in state["feedback_log"])

# ---------------------------------------------------------------------------
# 今日打卡
# ---------------------------------------------------------------------------
st.subheader(f"今日打卡 · {ui.day_label(state, today)}")
if not blocks_today:
    st.caption("今日没有排入复习任务。")
    if already_checked:
        st.info("今日反馈已提交。")
elif already_checked:
    st.info("今日反馈已提交。如需再次打卡，请先进入下一执行日，"
            "或在「复习计划」页手动重规划。")
else:
    by_course: dict[str, float] = {}
    for b in blocks_today:
        by_course[b["course"]] = by_course.get(b["course"], 0.0) \
            + float(b["hours"])
    st.markdown("**今日计划任务**")
    for course_name, hours in by_course.items():
        task_names = [b["task"] for b in blocks_today
                      if b["course"] == course_name]
        st.markdown(f"- {course_name}（{hours:.1f}h）："
                    + "、".join(sorted(set(task_names))))

    completion = {}
    for course_name in by_course:
        completion[course_name] = st.slider(
            f"{course_name} 完成率", 0, 100, 100, 5,
            key=f"rate-{today}-{course_name}",
            help="今日该课程计划任务的实际完成比例",
        ) / 100.0
    manual = st.checkbox(
        "同时手动请求重规划（绕过冷却期）", value=False,
        help="即使偏差不显著也强制重排；掌握度仍不受影响",
    )
    if st.button("提交今日反馈", type="primary"):
        try:
            outcome = es.checkin(state, today, completion, manual=manual)
        except (es.StateError, ValueError) as exc:
            st.error(f"反馈处理失败：{exc}")
        else:
            ui.persist()
            st.session_state["last-checkin"] = outcome
            st.rerun()

last = st.session_state.get("last-checkin")
if last:
    monitor = last["monitor"]
    st.markdown("**进度监控判定**")
    st.text(monitor["output"])
    if last["replanned"]:
        version = last["version"]
        st.success(
            f"已生成 v{version['version']}（{version['kind']}，"
            f"起于 Day {version['start_day']}）。"
        )
        st.markdown("**重规划后风险评分**")
        ui.render_risks(version.get("risks") or {})
        if version.get("allocation_diff"):
            st.markdown("**v1 / v2 分配差异**")
            st.dataframe(pd.DataFrame([
                {"Day": r["day"], "课程": r["course"], "v1": r["v1_hours"],
                 "v2": r["v2_hours"], "Δ": r["delta_hours"]}
                for r in version["allocation_diff"]
            ]), width="stretch", hide_index=True)
        ui.render_warnings(version.get("warnings") or [])

st.divider()

# ---------------------------------------------------------------------------
# 推进到下一执行日
# ---------------------------------------------------------------------------
st.subheader("执行日推进")
remaining_days = max(es.max_exam_day(state) - today, 0)
st.caption(
    f"当前 {ui.day_label(state, today)}；"
    f"最近一门考试在 {remaining_days} 天后。"
    "推进演示用：模拟时间流逝一天。"
)
if blocks_today and not already_checked:
    st.warning("今日还有未打卡的计划任务，建议先提交反馈再推进。")
if st.button("进入下一执行日 →"):
    state["current_day"] = today + 1
    st.session_state.pop("last-checkin", None)
    ui.persist()
    st.rerun()

# ---------------------------------------------------------------------------
# 反馈历史
# ---------------------------------------------------------------------------
if state["feedback_log"]:
    st.divider()
    st.subheader("反馈历史")
    rows = []
    for log in reversed(state["feedback_log"]):
        rates = "、".join(f"{k} {v:.0%}" for k, v in log["completion"].items())
        triggered = log["triggers"]
        rows.append({
            "执行日": f"Day {log['day']}",
            "日期": es.day_to_date(state, log["day"]),
            "完成率": rates,
            "判定": "触发重规划" if log["replanned"] else (
                "冷却期内抑制" if log["suppressed"] else "按计划继续"),
            "触发课程": "、".join(t["course"] for t in triggered) or "—",
        })
    st.dataframe(pd.DataFrame(rows), width="stretch",
                 hide_index=True)
