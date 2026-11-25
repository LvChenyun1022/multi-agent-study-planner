# -*- coding: utf-8 -*-
"""页面 1：课程与考试 —— 课程建档（结构化录入 / scope_text LLM 解析）。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import examos_state as es
import examos_ui as ui
from agents.exam_analysis_agent import ExamAnalysisAgent, ExamAnalysisError

ui.init_page("课程与考试", "📚")
state = ui.get_state()
ui.sidebar_status(state)

st.markdown('<div class="main-header">📚 课程与考试</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">登记课程档案与考试安排；所有结构化结果均经 '
    'CourseSpec 同源校验，与排程引擎入口完全一致。</div>',
    unsafe_allow_html=True,
)

today = int(state["current_day"])
origin = date.fromisoformat(state["origin_date"])


def _min_exam_date() -> date:
    return origin + timedelta(days=today + 1)


def _to_exam_day(exam_date: date) -> int:
    return (exam_date - origin).days


def _save_course(name: str, exam_day: int, credits: float, degree: bool,
                 priority: float, n_diag: int, min_hours: float,
                 a: float, b: float, knowledge_rows: list[dict],
                 task_rows: list[dict]) -> None:
    """经 ExamAnalysisAgent 结构化模式同源校验后建档。"""
    knowledge = {
        str(k["name"]).strip(): float(k["weight"])
        for k in knowledge_rows if str(k.get("name", "")).strip()
    }
    tasks = []
    for t in task_rows:
        task_name = str(t.get("name", "")).strip()
        if not task_name:
            continue
        deps = [d.strip() for d in str(t.get("deps", "")).split(",") if d.strip()]
        tasks.append({
            "name": task_name,
            "kp": str(t.get("kp", "")).strip(),
            "hours": float(t.get("hours", 0.0) or 0.0),
            "required": bool(t.get("required", True)),
            "deps": deps,
        })
    agent = ExamAnalysisAgent(None, None, None, llm=ui.get_llm())
    result = agent.run({
        "course_inputs": [{
            "name": name, "exam_day": exam_day, "credits": credits,
            "degree": degree, "priority": priority, "a": a, "b": b,
            "min_hours": min_hours, "knowledge": knowledge, "tasks": tasks,
        }],
        "reference_date": state["origin_date"],
    })
    spec = result["course_specs"][0]
    mastery_lookup = {
        str(k["name"]).strip(): float(k.get("mastery", 0.5))
        for k in knowledge_rows if str(k.get("name", "")).strip()
    }
    record = {
        "course_id": spec.course_id,
        "name": spec.name,
        "exam_day": spec.exam_day,
        "credits": spec.credits,
        "degree": spec.degree,
        "priority": spec.priority,
        "n_diag": int(n_diag),
        "a": spec.a,
        "b": spec.b,
        "min_hours": spec.min_hours,
        "knowledge": [
            {"name": kname, "weight": w,
             "mastery": min(1.0, max(0.0, mastery_lookup.get(kname, 0.5))),
             "mastery_source": "中性先验" if kname not in mastery_lookup else "自评"}
            for kname, w in spec.knowledge_weights.items()
        ],
        "tasks": [
            {"name": t.name, "kp": t.kp, "hours": t.hours,
             "required": t.required, "deps": list(t.deps)}
            for t in spec.tasks
        ],
        "task_state": {},
    }
    es.upsert_course(state, record)
    ui.persist()


# ---------------------------------------------------------------------------
# 已建档课程
# ---------------------------------------------------------------------------
st.subheader("已建档课程")
if not state["courses"]:
    st.caption("（空）")
for record in state["courses"]:
    exam_day = int(record["exam_day"])
    d_day = exam_day - today
    label = (f"{record['name']} · 考试 {ui.day_label(state, exam_day)} · "
             f"{'今天考试！' if d_day <= 0 else f'倒计时 {d_day} 天'}")
    with st.expander(label):
        total = sum(float(t["hours"]) for t in record["tasks"])
        done = sum(float(ts.get("done", 0.0))
                   for ts in (record.get("task_state") or {}).values())
        st.caption(
            f"学分 {record['credits']:g} · {'学位课' if record['degree'] else '非学位课'} · "
            f"优先级 {record['priority']:.2f} · n_diag {record['n_diag']} · "
            f"任务 {len(record['tasks'])} 项共 {total:.1f}h（已完成 {done:.1f}h）"
        )
        st.dataframe(pd.DataFrame([
            {"知识点": k["name"], "权重": k["weight"],
             "掌握度": k["mastery"], "来源": k.get("mastery_source", "中性先验")}
            for k in record["knowledge"]
        ]), width="stretch", hide_index=True)
        st.dataframe(pd.DataFrame([
            {"任务": t["name"], "知识点": t["kp"], "工时": t["hours"],
             "类型": "必修" if t["required"] else "可选",
             "依赖": ", ".join(t.get("deps", []))}
            for t in record["tasks"]
        ]), width="stretch", hide_index=True)
        if st.button(f"删除 {record['name']}", key=f"del-{record['name']}"):
            es.remove_course(state, record["name"])
            ui.persist()
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# 新增课程
# ---------------------------------------------------------------------------
st.subheader("新增课程")
tab_struct, tab_llm = st.tabs(["结构化录入", "scope_text 智能解析（LLM）"])

KNOWLEDGE_COLUMNS = {
    "name": st.column_config.TextColumn("知识点", required=True),
    "weight": st.column_config.NumberColumn("考试权重", min_value=0.01,
                                            step=0.1, required=True),
    "mastery": st.column_config.NumberColumn("当前掌握度（自评 0–1）",
                                             min_value=0.0, max_value=1.0,
                                             step=0.05),
}
TASK_COLUMNS = {
    "name": st.column_config.TextColumn("任务名", required=True),
    "kp": st.column_config.TextColumn("所属知识点", required=True),
    "hours": st.column_config.NumberColumn("预计工时(h)", min_value=0.0,
                                           step=0.5, required=True),
    "required": st.column_config.CheckboxColumn("必修", default=True),
    "deps": st.column_config.TextColumn("前置任务（逗号分隔）", default=""),
}


def _common_inputs(prefix: str):
    col1, col2 = st.columns(2)
    name = col1.text_input("课程名称", key=f"{prefix}-name")
    exam_date = col2.date_input("考试日期", value=_min_exam_date(),
                                min_value=_min_exam_date(), key=f"{prefix}-date")
    col1, col2, col3 = st.columns(3)
    credits = col1.number_input("学分", min_value=0.5, value=3.0, step=0.5,
                                key=f"{prefix}-credits")
    degree = col2.checkbox("学位课", value=True, key=f"{prefix}-degree")
    priority = col3.slider("自标优先级", 0.0, 1.0, 0.5, 0.01,
                           key=f"{prefix}-priority")
    with st.expander("高级参数（收益函数与诊断证据量）"):
        col1, col2, col3 = st.columns(3)
        n_diag = col1.number_input("已有诊断题量 n_diag", min_value=0, value=0,
                                   step=1, key=f"{prefix}-ndiag")
        min_hours = col2.number_input("最低复习保障(h)", min_value=0.0,
                                      value=0.0, step=0.5,
                                      key=f"{prefix}-minh")
        a = col3.number_input("收益上限 a", min_value=0.1, value=2.0, step=0.1,
                              key=f"{prefix}-a")
        b = st.number_input("学习效率 b", min_value=0.01, value=0.5, step=0.05,
                            key=f"{prefix}-b")
    return dict(name=name, exam_date=exam_date, credits=credits, degree=degree,
                priority=priority, n_diag=int(n_diag), min_hours=min_hours,
                a=a, b=b)


with tab_struct:
    common = _common_inputs("struct")
    st.markdown("**知识点与当前掌握度**（掌握度默认 0.5 为中性先验，"
                "未经测验证据校准）")
    knowledge_df = st.data_editor(
        pd.DataFrame([{"name": "", "weight": 1.0, "mastery": 0.5}]),
        column_config=KNOWLEDGE_COLUMNS, num_rows="dynamic",
        width="stretch", key="struct-knowledge",
    )
    st.markdown("**复习任务**（kp 必须是上方已声明的知识点；deps 只引用本课程任务）")
    tasks_df = st.data_editor(
        pd.DataFrame([{"name": "", "kp": "", "hours": 1.0,
                       "required": True, "deps": ""}]),
        column_config=TASK_COLUMNS, num_rows="dynamic",
        width="stretch", key="struct-tasks",
    )
    if st.button("校验并保存课程", type="primary", key="struct-save"):
        try:
            if not common["name"].strip():
                raise ExamAnalysisError("课程名称不能为空")
            _save_course(
                common["name"].strip(), _to_exam_day(common["exam_date"]),
                common["credits"], common["degree"], common["priority"],
                common["n_diag"], common["min_hours"], common["a"], common["b"],
                knowledge_df.to_dict("records"), tasks_df.to_dict("records"),
            )
        except (ExamAnalysisError, es.StateError, ValueError) as exc:
            st.error(f"校验未通过：{exc}")
        else:
            st.success(f"课程《{common['name'].strip()}》已建档。")
            st.rerun()

with tab_llm:
    common = _common_inputs("llm")
    scope_text = st.text_area(
        "考试范围描述（自然语言）",
        placeholder="例如：第 3–5 章，重点是特征值与特征向量（约占 40%）、"
                    "二次型（约占 30%），其余为矩阵运算基础；"
                    "老师强调要看历年卷第三大题……",
        height=140, key="llm-scope",
    )
    hint = st.text_input("补充说明（可选）", key="llm-hint")
    if st.button("LLM 解析考试范围", key="llm-parse"):
        if not common["name"].strip() or not scope_text.strip():
            st.error("请先填写课程名称与考试范围描述。")
        else:
            agent = ExamAnalysisAgent(None, None, None, llm=ui.get_llm())
            try:
                with st.spinner("正在解析考试范围……" + ui.LLM_LATENCY_HINT):
                    result = agent.run({
                        "course_inputs": [{
                            "name": common["name"].strip(),
                            "exam_day": _to_exam_day(common["exam_date"]),
                            "credits": common["credits"],
                            "degree": common["degree"],
                            "priority": common["priority"],
                            "a": common["a"], "b": common["b"],
                            "min_hours": common["min_hours"],
                            "scope_text": scope_text, "hint": hint,
                        }],
                        "reference_date": state["origin_date"],
                    })
            except ExamAnalysisError as exc:
                st.error(f"解析失败：{exc}")
            else:
                spec = result["course_specs"][0]
                st.session_state["llm-draft"] = {
                    "common": common, "spec": spec,
                    "output": result["output"],
                }
    draft = st.session_state.get("llm-draft")
    if draft:
        st.info("以下为 LLM 抽取结果【待确认】：请核对权重与工时后再保存。"
                "日期换算、结构校验由确定性代码完成。")
        st.markdown(draft["output"])
        spec = draft["spec"]
        knowledge_df = st.data_editor(
            pd.DataFrame([
                {"name": k, "weight": w, "mastery": 0.5}
                for k, w in spec.knowledge_weights.items()
            ]),
            column_config=KNOWLEDGE_COLUMNS, num_rows="dynamic",
            width="stretch", key="llm-knowledge",
        )
        tasks_df = st.data_editor(
            pd.DataFrame([
                {"name": t.name, "kp": t.kp, "hours": t.hours,
                 "required": t.required, "deps": ", ".join(t.deps)}
                for t in spec.tasks
            ]),
            column_config=TASK_COLUMNS, num_rows="dynamic",
            width="stretch", key="llm-tasks",
        )
        col1, col2 = st.columns(2)
        if col1.button("确认并保存课程", type="primary", key="llm-save"):
            try:
                _save_course(
                    spec.name, spec.exam_day, spec.credits, spec.degree,
                    spec.priority, draft["common"]["n_diag"], spec.min_hours,
                    spec.a, spec.b,
                    knowledge_df.to_dict("records"), tasks_df.to_dict("records"),
                )
            except (ExamAnalysisError, es.StateError, ValueError) as exc:
                st.error(f"校验未通过：{exc}")
            else:
                del st.session_state["llm-draft"]
                st.success(f"课程《{spec.name}》已建档。")
                st.rerun()
        if col2.button("丢弃解析结果", key="llm-discard"):
            del st.session_state["llm-draft"]
            st.rerun()
