
# -*- coding: utf-8 -*-
"""app_parallel.py —— Streamlit 前端:演示同学B的调度扩展(并行/重试/分支/更多Agent)。

运行:``streamlit run app_parallel.py``

相比原 app.py,本页面额外演示:
1. 调度模式切换:串行(默认 3 Agent) vs 并行(6 Agent DAG);
2. 并行调度:诊断/学情摘要/不稳定 三个无依赖节点第一波并发;
3. 失败重试:不稳定Agent首次必失败,重试后成功,日志可见指数退避;
4. 条件分支:勾选「参加竞赛」→ 竞赛拔高Agent运行;不勾 → 跳过;
5. 更多Agent:学情摘要Agent / 不稳定Agent / 竞赛拔高Agent。
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import streamlit as st

from context_store import ContextStore
from mcp_server import MCPServer
from harness import Harness
from agents import BaseAgent, DiagnoseAgent, PlannerAgent, TutorAgent
from orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# 新增演示 Agent(与 demo_parallel.py 一致)
# ---------------------------------------------------------------------------
class 学情摘要Agent(BaseAgent):
    """新 Agent 示例:从学生档案生成一行学情摘要,与诊断 Agent 并行执行。"""

    name = "学情摘要Agent"

    def run(self, context: dict) -> dict:
        profile = self.store.snapshot().get("user_profile", {})
        姓名 = profile.get("姓名", "学生")
        学科 = profile.get("薄弱学科", "未知学科")
        运动 = "、".join(profile.get("运动", []) or []) or "无"
        output = f"【学情摘要】{姓名}:薄弱学科为{学科};运动安排:{运动}。"
        return {"output": output, "skill_used": "summary"}


class 不稳定Agent(BaseAgent):
    """演示失败重试:每次会话首次运行抛异常,第二次成功(配合 max_retries=1)。"""

    name = "不稳定Agent"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._attempts = 0

    def run(self, context: dict) -> dict:
        self._attempts += 1
        if self._attempts == 1:
            raise RuntimeError("第一次运行失败(演示重试机制)")
        return {
            "output": f"【不稳定Agent】第 {self._attempts} 次尝试成功。",
            "attempts": self._attempts,
        }


class 竞赛拔高Agent(BaseAgent):
    """演示条件分支:仅当学生档案含「竞赛」字样时才运行(由 condition 控制)。"""

    name = "竞赛拔高Agent"

    def run(self, context: dict) -> dict:
        return {"output": "【竞赛拔高】安排竞赛专题训练,每周 2 次,主攻综合难题。"}


def 需要竞赛拔高(outputs: dict, store: ContextStore) -> bool:
    """节点 condition:学生档案含「竞赛」字样才运行竞赛拔高Agent。"""
    profile = store.snapshot().get("user_profile", {})
    return "竞赛" in json.dumps(profile, ensure_ascii=False)


def 构建并行计划() -> list[dict]:
    """6 节点 DAG:三个无依赖节点第一波并行,竞赛拔高受 condition 控制。"""
    return [
        {"name": "诊断Agent", "depends_on": [], "max_retries": 2, "condition": None},
        {"name": "学情摘要Agent", "depends_on": [], "max_retries": 2, "condition": None},
        {"name": "不稳定Agent", "depends_on": [], "max_retries": 1, "condition": None},
        {"name": "竞赛拔高Agent", "depends_on": ["诊断Agent"], "max_retries": 0,
         "condition": 需要竞赛拔高},
        {"name": "规划Agent", "depends_on": ["诊断Agent", "学情摘要Agent"],
         "max_retries": 2, "condition": None},
        {"name": "辅导Agent", "depends_on": ["规划Agent"], "max_retries": 2,
         "condition": None},
    ]


def 构建系统(计划: list[dict] | None):
    """每次点击都重建系统,保证不稳定Agent的失败计数器是干净的。"""
    store = ContextStore("context_store_web.json")
    mcp = MCPServer()
    harness = Harness(store)
    agents = {
        "诊断Agent": DiagnoseAgent(mcp, store, harness),
        "学情摘要Agent": 学情摘要Agent(mcp, store, harness),
        "不稳定Agent": 不稳定Agent(mcp, store, harness),
        "竞赛拔高Agent": 竞赛拔高Agent(mcp, store, harness),
        "规划Agent": PlannerAgent(mcp, store, harness),
        "辅导Agent": TutorAgent(mcp, store, harness),
    }
    orch = Orchestrator(agents, store, task_plan=计划, max_workers=3)
    return store, orch


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
st.set_page_config(page_title="多Agent调度演示", layout="wide")
st.title("🎓 多Agent学习规划协作系统 · 调度扩展演示")
st.caption(
    "Orchestrator 按 DAG 依赖调度 → 并行执行无依赖节点 → 失败指数退避重试 → "
    "condition 条件分支跳过/运行 → 敏感工具经 Harness 人工审批"
)

with st.sidebar:
    st.header("调度设置")
    模式 = st.radio(
        "调度模式",
        ["并行(6 Agent DAG)", "串行(3 Agent 默认)"],
        index=0,
        help="并行模式演示失败重试/条件分支/更多Agent;串行模式对比原版固定顺序。",
    )
    st.divider()
    st.header("学生信息")
    with st.form("profile_form"):
        姓名 = st.text_input("姓名", value="小明")
        薄弱学科 = st.selectbox("薄弱学科", ["英语", "数学", "物理", "化学", "语文"])
        运动 = st.multiselect(
            "运动安排",
            ["网球(周二、周四)", "游泳(周六)", "篮球(周五)", "跑步(每天)"],
            default=["网球(周二、周四)", "游泳(周六)"],
        )
        备注 = st.text_area("备注", value="周三晚上效率低")
        参加竞赛 = st.checkbox("该生参加学科竞赛(演示条件分支)", value=False)
        提交 = st.form_submit_button("开始规划", use_container_width=True)

if 提交:
    user_profile = {
        "姓名": 姓名,
        "薄弱学科": 薄弱学科,
        "运动": 运动,
        "城市": "上海",
        "备注": (备注 + ";参加学科竞赛") if 参加竞赛 else 备注,
    }
    计划 = 构建并行计划() if "并行" in 模式 else None
    store, orch = 构建系统(计划)

    日志缓冲 = io.StringIO()
    with st.spinner("Agent 协作中 …"):
        with redirect_stdout(日志缓冲):
            try:
                snapshot = orch.run_session(f"web-session-{姓名}", user_profile)
                错误 = None
            except Exception as e:
                snapshot = store.snapshot()
                错误 = str(e)
    st.session_state["调度日志"] = 日志缓冲.getvalue()
    st.session_state["最终快照"] = snapshot
    st.session_state["运行错误"] = 错误
    if 错误:
        st.error(f"会话异常: {错误}")
    else:
        st.success("规划完成!")

# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------
快照 = st.session_state.get("最终快照")
日志 = st.session_state.get("调度日志", "")

if 快照 is None:
    st.info("👈 在左侧填写学生信息并点击「开始规划」,即可看到并行调度、重试与分支的实时效果。")
else:
    左栏, 右栏 = st.columns([1, 1])
    with 左栏:
        st.subheader("📜 调度日志")
        st.code(日志, language="text")
    with 右栏:
        st.subheader("🧭 任务链状态")
        任务链 = 快照.get("task_chain", [])
        基础键 = {"agent", "status", "input", "input_note", "output", "extra"}
        for 任务 in 任务链:
            extra = 任务.get("extra") or {}
            if extra.get("skipped"):
                徽章 = "⏭ 已跳过"
            elif extra.get("failed"):
                徽章 = "❌ 失败"
            else:
                徽章 = "✅ 完成"
            输入 = 任务.get("input", "")
            with st.expander(f"{徽章} {任务.get('agent')} — {输入}", expanded=True):
                st.markdown(任务.get("output", ""))
                附加 = dict(extra)
                附加.update({k: v for k, v in 任务.items() if k not in 基础键})
                if 附加:
                    st.caption("附加信息: " + json.dumps(附加, ensure_ascii=False))

    with st.expander("📦 查看完整 Context Store 快照", expanded=False):
        st.json(快照)
