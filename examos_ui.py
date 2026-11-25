# -*- coding: utf-8 -*-
"""ExamOS 前端共享层：Streamlit 页面公用的状态存取、LLM 接入与组件。"""
from __future__ import annotations

import streamlit as st

import examos_state as es
from llm_client import create_llm_from_config

CSS = """
<style>
    .main-header { font-size: 2.0rem; font-weight: 700; margin-bottom: 0.1rem; }
    .sub-header { font-size: 0.95rem; color: #666; margin-bottom: 1.2rem; }
    .stat-box {
        text-align: center; padding: 0.9rem;
        background: #f8f9fa; border-radius: 10px; border: 1px solid #e9ecef;
    }
    .stat-box .num { font-size: 1.8rem; font-weight: 700; color: #4a90d9; }
    .stat-box .label { font-size: 0.85rem; color: #666; }
    .warn-box {
        border-left: 4px solid #e8a33d; background: #fff8ec;
        padding: 0.6rem 1rem; border-radius: 0 8px 8px 0; margin-bottom: 0.4rem;
    }
    .ok-box {
        border-left: 4px solid #3d9970; background: #f0fff4;
        padding: 0.6rem 1rem; border-radius: 0 8px 8px 0; margin-bottom: 0.4rem;
    }
</style>
"""

LLM_LATENCY_HINT = (
    "致远一号（校园网模型服务）响应较慢，长文本可能需要 1–3 分钟，请耐心等待；"
    "期间请勿重复点击。"
)


def init_page(title: str, icon: str = "🎓") -> None:
    st.set_page_config(page_title=f"{title} · ExamOS", page_icon=icon,
                       layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)


def get_state() -> dict:
    if "examos_state" not in st.session_state:
        st.session_state["examos_state"] = es.load_state()
    return st.session_state["examos_state"]


def persist() -> None:
    es.save_state(get_state())


def reset_state() -> None:
    st.session_state["examos_state"] = es.default_state()
    persist()


def get_llm():
    """config.json 的 llm 段齐全 → 真实 API（致远一号）；否则 MockLLM。"""
    if "examos_llm" not in st.session_state:
        st.session_state["examos_llm"] = create_llm_from_config()
    return st.session_state["examos_llm"]


def llm_mode_label() -> str:
    llm = get_llm()
    if llm is None:
        return "MockLLM（离线规则模板）"
    return f"真实模型 `{llm.model}`（{llm.base_url}）"


def day_label(state: dict, day: int) -> str:
    return f"Day {day}（{es.day_to_date(state, day)}）"


def sidebar_status(state: dict) -> None:
    st.sidebar.header("ExamOS 2.0")
    st.sidebar.caption(f"规划原点：{state['origin_date']}")
    st.sidebar.caption(f"当前执行日：{day_label(state, state['current_day'])}")
    st.sidebar.caption(f"LLM：{llm_status_short()}")
    plan = es.current_plan(state)
    if plan:
        st.sidebar.caption(
            f"当前计划：v{plan['version']}（{plan['kind']}，"
            f"起于 Day {plan['start_day']}）"
        )
    else:
        st.sidebar.caption("当前计划：尚未生成")


def llm_status_short() -> str:
    llm = get_llm()
    return "MockLLM" if llm is None else f"致远一号 {llm.model}"


def render_risks(risks: dict) -> None:
    import pandas as pd
    rows = [
        {"课程": name, "D 紧迫度": v["D"], "M 掌握缺口": v["M"],
         "W 重要度": v["W"], "U 不确定性": v["U"], "R 综合风险": v["R"]}
        for name, v in risks.items()
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch",
                 hide_index=True)


def render_warnings(warnings: list) -> None:
    if not warnings:
        st.markdown('<div class="ok-box">无未解决的排程预警。</div>',
                    unsafe_allow_html=True)
        return
    for warning in warnings:
        st.markdown(f'<div class="warn-box">{warning}</div>',
                    unsafe_allow_html=True)


def render_schedule_by_task(state: dict, plan: dict) -> None:
    """任务聚合视图：每个任务一块，列出各日分配与 总量/完成/剩余。"""
    schedule = plan.get("schedule") or {}
    if not schedule:
        st.info("当前时间窗口内没有可分配任务。")
        return
    planned: dict[tuple, dict[int, float]] = {}
    for day, blocks in schedule.items():
        for b in blocks:
            key = (b["course"], b["task"])
            planned.setdefault(key, {})[int(day)] = \
                planned.get(key, {}).get(int(day), 0.0) + float(b["hours"])
    for record in state["courses"]:
        course_tasks = [(k, v) for k, v in planned.items()
                        if k[0] == record["name"]]
        if not course_tasks:
            continue
        st.markdown(f"**{record['name']}**")
        for (course_name, task_name), days in course_tasks:
            task = next((t for t in record["tasks"] if t["name"] == task_name),
                        None)
            if task is None:
                continue
            ts = (record.get("task_state") or {}).get(
                task_name, {"done": 0.0, "remaining": task["hours"]})
            kind = "必修" if task["required"] else "可选"
            total_planned = sum(days.values())
            day_text = "，".join(
                f"Day {d} {h:.1f}h" for d, h in sorted(days.items()))
            st.markdown(
                f"- 【{task_name}】{kind}（{task['kp']}）：{day_text}　|　"
                f"总量 {float(task['hours']):.1f}h / 已完成 {float(ts['done']):.1f}h / "
                f"本计划分配 {total_planned:.1f}h / 未安排 {float(ts['remaining']):.1f}h"
            )
