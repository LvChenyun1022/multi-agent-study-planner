# -*- coding: utf-8 -*-
"""页面 4：诊断与画像 —— 诊断 Agent、掌握度与诊断证据量维护。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import examos_state as es
import examos_ui as ui
from context_store import ContextStore
from harness import Harness
from mcp_server import MCPServer
from agents import DiagnoseAgent
from scheduler import mastery_gap, uncertainty

ui.init_page("诊断与画像", "🧭")
state = ui.get_state()
ui.sidebar_status(state)

st.markdown('<div class="main-header">🧭 诊断与画像</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">诊断 Agent 经 RAG 检索生成诊断报告；'
    '掌握度与 n_diag 是风险评分 M / U 因子的输入，'
    '其变更会在下一次执行反馈时作为重规划信号。</div>',
    unsafe_allow_html=True,
)

if not state["courses"]:
    st.info("尚未登记课程。请先在「课程与考试」页建档。")
    st.stop()

today = int(state["current_day"])

# ---------------------------------------------------------------------------
# 画像总览
# ---------------------------------------------------------------------------
st.subheader("课程画像总览")
overview_rows = []
for record in state["courses"]:
    knowledge = [(k["name"], float(k["weight"]), float(k["mastery"]))
                 for k in record["knowledge"]]
    sw = sum(w for _, w, _ in knowledge)
    swm = sum(w * m for _, w, m in knowledge)
    gap = 1.0 - swm / sw if sw else 1.0
    overview_rows.append({
        "课程": record["name"],
        "考试日": ui.day_label(state, int(record["exam_day"])),
        "M 掌握缺口": round(gap, 3),
        "U 不确定性": round(uncertainty(int(record["n_diag"])), 3),
        "n_diag": int(record["n_diag"]),
    })
st.dataframe(pd.DataFrame(overview_rows), width="stretch",
             hide_index=True)
st.caption("M = 1 − Σ(权重×掌握度)/Σ权重；U = 1/√(1+n_diag)。"
           "掌握度为自评或中性先验，未经真实测验证据校准。")

st.divider()

# ---------------------------------------------------------------------------
# 诊断 Agent
# ---------------------------------------------------------------------------
st.subheader("学习诊断（诊断 Agent · RAG）")
col1, col2 = st.columns([1, 2])
course_name = col1.selectbox(
    "选择课程", [c["name"] for c in state["courses"]], key="diag-course")
note = col2.text_input("备注（如最近错题类型、自我感受）", key="diag-note")
if st.button("运行诊断", type="primary"):
    store = ContextStore()
    agent = DiagnoseAgent(MCPServer(), store, Harness(store),
                          llm=ui.get_llm())
    try:
        with st.spinner("诊断 Agent 检索与生成中……" + ui.LLM_LATENCY_HINT):
            result = agent.run({
                "user_profile": {"薄弱学科": course_name, "备注": note},
            })
    except Exception as exc:  # noqa: BLE001 —— 前端兜底展示
        st.error(f"诊断失败：{exc}")
    else:
        st.session_state["diag-result"] = result

diag = st.session_state.get("diag-result")
if diag:
    st.markdown(diag["output"])
    if diag.get("rag_sources"):
        st.caption("RAG 来源：" + "、".join(diag["rag_sources"]))

st.divider()

# ---------------------------------------------------------------------------
# 掌握度与诊断证据量维护
# ---------------------------------------------------------------------------
st.subheader("掌握度与诊断证据量")
st.caption(
    "掌握度纪律：系统不会因为你没完成计划而降低掌握度；"
    "此处修改视为自评更新，保存后将作为「掌握度变化」信号参与下一次"
    "重规划判定。正式部署后应由测验证据自动更新。"
)
for record in state["courses"]:
    with st.expander(f"{record['name']}"):
        knowledge_df = st.data_editor(
            pd.DataFrame([
                {"知识点": k["name"], "权重": float(k["weight"]),
                 "掌握度": float(k["mastery"]),
                 "来源": k.get("mastery_source", "中性先验")}
                for k in record["knowledge"]
            ]),
            column_config={
                "知识点": st.column_config.TextColumn("知识点", disabled=True),
                "权重": st.column_config.NumberColumn("权重", disabled=True),
                "掌握度": st.column_config.NumberColumn(
                    "掌握度（0–1）", min_value=0.0, max_value=1.0, step=0.05),
                "来源": st.column_config.TextColumn("来源", disabled=True),
            },
            width="stretch", hide_index=True,
            key=f"mastery-{record['name']}",
        )
        n_diag = st.number_input(
            "有效诊断题量 n_diag", min_value=0, step=1,
            value=int(record["n_diag"]), key=f"ndiag-{record['name']}",
        )
        if st.button("保存画像更新", key=f"save-{record['name']}"):
            changed = False
            for row, k in zip(knowledge_df.to_dict("records"),
                              record["knowledge"]):
                new_value = float(row["掌握度"])
                if abs(new_value - float(k["mastery"])) > 1e-9:
                    k["mastery"] = new_value
                    k["mastery_source"] = "自评"
                    changed = True
            if int(n_diag) != int(record["n_diag"]):
                record["n_diag"] = int(n_diag)
                changed = True
            if changed:
                pending = state.setdefault("mastery_changed_pending", [])
                if record["name"] not in pending:
                    pending.append(record["name"])
                ui.persist()
                st.success(
                    f"《{record['name']}》画像已更新；将在下一次执行反馈时"
                    "作为重规划信号。"
                )
            else:
                st.caption("没有变化。")
