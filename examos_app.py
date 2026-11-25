# -*- coding: utf-8 -*-
"""ExamOS 2.0 —— 今日仪表盘（Streamlit 多页应用入口）。

五大页面：
- 本页（今日仪表盘）：今日任务、风险评分、预警一览；
- pages/1_课程与考试：课程建档（结构化录入 / scope_text LLM 解析）；
- pages/2_复习计划：每日时间预算、生成/重排计划、版本历史；
- pages/3_执行反馈：按日打卡 → 进度监控 → 显著偏差触发重规划；
- pages/4_诊断与画像：诊断 Agent、掌握度与 n_diag 维护。

运行：``streamlit run examos_app.py``
"""
from __future__ import annotations

import streamlit as st

import examos_state as es
import examos_ui as ui

ui.init_page("今日仪表盘", "📊")
state = ui.get_state()
ui.sidebar_status(state)

st.markdown('<div class="main-header">📊 ExamOS 今日仪表盘</div>',
            unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">多智能体考试复习规划系统 · '
    '风险感知排程 · 执行反馈驱动的动态重规划</div>',
    unsafe_allow_html=True,
)

today = int(state["current_day"])
plan = es.current_plan(state)

# ---------------------------------------------------------------------------
# 顶部指标
# ---------------------------------------------------------------------------
blocks_today = es.plan_blocks(plan, today)
hours_today = sum(float(b["hours"]) for b in blocks_today)
unfinished_required = 0
for record in es.active_courses(state, today):
    for task in record["tasks"]:
        if task["required"] and es.task_remaining(record, task["name"]) > 1e-9:
            unfinished_required += 1
warning_count = len((plan or {}).get("warnings") or [])

col1, col2, col3, col4 = st.columns(4)
col1.markdown(f'<div class="stat-box"><div class="num">{len(state["courses"])}</div>'
              f'<div class="label">已建档课程</div></div>', unsafe_allow_html=True)
col2.markdown(f'<div class="stat-box"><div class="num">{hours_today:.1f}h</div>'
              f'<div class="label">今日计划时长</div></div>', unsafe_allow_html=True)
col3.markdown(f'<div class="stat-box"><div class="num">{unfinished_required}</div>'
              f'<div class="label">未完成必修任务</div></div>', unsafe_allow_html=True)
col4.markdown(f'<div class="stat-box"><div class="num">{warning_count}</div>'
              f'<div class="label">排程预警</div></div>', unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------------------------
# 今日任务
# ---------------------------------------------------------------------------
st.subheader(f"今日任务 · {ui.day_label(state, today)}")
if not state["courses"]:
    st.info("尚未登记课程。请先在「课程与考试」页建档。")
elif plan is None:
    st.info("课程已建档，但尚未生成复习计划。请前往「复习计划」页。")
elif not blocks_today:
    done_today = any(log["day"] == today for log in state["feedback_log"])
    if done_today:
        st.success("今日执行反馈已提交，计划已进入下一执行日窗口。")
    else:
        st.caption("今日没有排入复习任务。")
else:
    by_course: dict[str, list[dict]] = {}
    for b in blocks_today:
        by_course.setdefault(b["course"], []).append(b)
    for course_name, blocks in by_course.items():
        total = sum(float(b["hours"]) for b in blocks)
        with st.expander(f"{course_name} · 今日共 {total:.1f}h", expanded=True):
            import pandas as pd
            st.dataframe(
                pd.DataFrame([
                    {"任务": b["task"], "分配时长": f"{float(b['hours']):.1f}h"}
                    for b in blocks
                ]),
                width="stretch", hide_index=True,
            )
    st.caption("完成今日学习后，请到「执行反馈」页打卡；"
               "显著偏差将自动触发重规划。")

# ---------------------------------------------------------------------------
# 风险评分与预警
# ---------------------------------------------------------------------------
if plan:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("课程风险评分（D / M / W / U → R）")
        ui.render_risks(plan.get("risks") or {})
    with right:
        st.subheader("预警与剩余风险")
        ui.render_warnings(plan.get("warnings") or [])

# ---------------------------------------------------------------------------
# 最近执行反馈
# ---------------------------------------------------------------------------
if state["feedback_log"]:
    st.subheader("最近执行反馈")
    for log in reversed(state["feedback_log"][-3:]):
        verdict = "触发重规划" if log["replanned"] else "按计划继续"
        rates = "、".join(f"{k} {v:.0%}" for k, v in log["completion"].items())
        st.caption(
            f"Day {log['day']}（{es.day_to_date(state, log['day'])}）："
            f"{rates} → {verdict}"
        )

# ---------------------------------------------------------------------------
# 数据管理
# ---------------------------------------------------------------------------
with st.sidebar.expander("⚠️ 数据管理"):
    st.caption("清空课程档案、计划版本与执行记录（不可恢复）。")
    confirm = st.checkbox("确认清空全部数据")
    if st.button("清空并重置", disabled=not confirm):
        ui.reset_state()
        st.rerun()
