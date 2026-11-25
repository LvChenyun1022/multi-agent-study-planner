# -*- coding: utf-8 -*-
"""页面 2：复习计划 —— 时间预算维护、生成/重排计划、版本历史。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import examos_state as es
import examos_ui as ui

ui.init_page("复习计划", "🗓️")
state = ui.get_state()
ui.sidebar_status(state)

st.markdown('<div class="main-header">🗓️ 复习计划</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">每日可用时间由你按真实约束显式提供'
    '（系统不猜测空闲时间）；重排只退回当前及以后的未执行计划块。</div>',
    unsafe_allow_html=True,
)

today = int(state["current_day"])
plan = es.current_plan(state)

if not state["courses"]:
    st.info("尚未登记课程。请先在「课程与考试」页建档。")
    st.stop()

# ---------------------------------------------------------------------------
# 每日可用时间
# ---------------------------------------------------------------------------
st.subheader("每日可用时间（小时）")
horizon = max(es.max_exam_day(state), today + 1)
day_rows = []
for day in range(today, horizon):
    day_rows.append({
        "day": day,
        "日期": es.day_to_date(state, day),
        "可用小时": float(state["daily_hours"].get(str(day),
                                              es.DEFAULT_DAILY_HOURS)),
    })
hours_df = st.data_editor(
    pd.DataFrame(day_rows),
    column_config={
        "day": st.column_config.NumberColumn("Day", disabled=True),
        "日期": st.column_config.TextColumn("日期", disabled=True),
        "可用小时": st.column_config.NumberColumn(
            "可用小时", min_value=0.0, max_value=16.0, step=0.5),
    },
    width="stretch", hide_index=True, key="daily-hours-editor",
)
new_daily = {str(int(r["day"])): float(r["可用小时"])
             for r in hours_df.to_dict("records")}
hours_changed = any(
    float(state["daily_hours"].get(d, es.DEFAULT_DAILY_HOURS)) != h
    for d, h in new_daily.items()
)

col1, col2, col3 = st.columns(3)
if plan is None:
    if col1.button("生成初始复习计划", type="primary"):
        state["daily_hours"].update(new_daily)
        try:
            version = es.initial_plan(state, today)
        except (es.StateError, ValueError) as exc:
            st.error(f"排程失败：{exc}")
        else:
            ui.persist()
            st.success(f"已生成计划 v{version['version']}。")
            st.rerun()
else:
    if col1.button("保存时间预算", disabled=not hours_changed):
        state["daily_hours"].update(new_daily)
        ui.persist()
        st.success("时间预算已保存（未改动计划）。")
        st.rerun()
    if col2.button("保存并按新约束重排", disabled=not hours_changed):
        state["daily_hours"].update(new_daily)
        try:
            version = es.replan(state, today, kind="约束变化重规划")
        except (es.StateError, ValueError) as exc:
            st.error(f"重排失败：{exc}")
        else:
            ui.persist()
            st.success(f"已按新时间预算重排：计划 v{version['version']}。")
            st.rerun()
    if col3.button("手动重规划（退回未来块重排）"):
        try:
            version = es.replan(state, today, kind="手动重规划")
        except (es.StateError, ValueError) as exc:
            st.error(f"重排失败：{exc}")
        else:
            ui.persist()
            st.success(f"已生成计划 v{version['version']}。")
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# 当前计划
# ---------------------------------------------------------------------------
if plan is None:
    st.info("尚未生成复习计划。设置每日可用时间后点击「生成初始复习计划」。")
    st.stop()

st.subheader(
    f"当前计划 v{plan['version']} · {plan['kind']} · "
    f"起于 {ui.day_label(state, plan['start_day'])}"
)
badge = "✅ 通过" if plan.get("conservation_ok") else "❌ 失败"
st.caption(f"守恒校验（总量 = 已完成 + 已分配 + 未安排）：{badge}")

st.markdown("**课程风险评分**")
ui.render_risks(plan.get("risks") or {})

st.markdown("**任务分配（按任务聚合）**")
ui.render_schedule_by_task(state, plan)

st.markdown("**预警与剩余风险**")
ui.render_warnings(plan.get("warnings") or [])

if plan.get("feedback"):
    with st.expander("本版本触发时的执行反馈明细"):
        st.dataframe(pd.DataFrame([
            {"课程": r["course"], "任务": r["task"],
             "原计划": r["planned_hours"], "完成": r["completed_hours"],
             "回补": r["returned_hours"]}
            for r in plan["feedback"]
        ]), width="stretch", hide_index=True)

if plan.get("allocation_diff"):
    with st.expander("v1 / v2 分配差异"):
        st.dataframe(pd.DataFrame([
            {"Day": r["day"], "课程": r["course"], "v1": r["v1_hours"],
             "v2": r["v2_hours"], "Δ": r["delta_hours"]}
            for r in plan["allocation_diff"]
        ]), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 版本历史
# ---------------------------------------------------------------------------
st.divider()
st.subheader("计划版本历史")
history_rows = [
    {"版本": f"v{v['version']}", "类型": v["kind"], "模式": v["mode"],
     "起始日": f"Day {v['start_day']}",
     "创建时间": v["created_at"][:19].replace("T", " "),
     "守恒": "通过" if v["conservation_ok"] else "失败"}
    for v in state["plan_versions"]
]
st.dataframe(pd.DataFrame(history_rows), width="stretch",
             hide_index=True)
if len(state["plan_versions"]) > 1:
    choice = st.selectbox(
        "查看历史版本",
        options=list(range(len(state["plan_versions"]))),
        format_func=lambda i: (
            f"v{state['plan_versions'][i]['version']} · "
            f"{state['plan_versions'][i]['kind']}"
        ),
        index=len(state["plan_versions"]) - 1,
    )
    chosen = state["plan_versions"][choice]
    if chosen is not plan:
        st.caption(f"v{chosen['version']} 详情（历史版本，只读）")
        ui.render_risks(chosen.get("risks") or {})
        ui.render_schedule_by_task(state, chosen)
        ui.render_warnings(chosen.get("warnings") or [])
