"""app.py —— Streamlit 前端：多Agent学习规划协作系统（美化版 + 审批历史 + 长期Memory）。

页面结构（以同学D美化版为基础，整合既有功能）：
1. 🎨 **前端美化**：Tab 页面布局 + 卡片样式 + 彩色状态标签 + Agent 流程图
   （状态映射补全 failed ❌ / skipped ⏭）；
2. 🔌 **真实LLM接线**：config.json 的 "llm" 段齐全则走真实 API，
   否则回退 MockLLM；页头显示当前 LLM 模式（不显示 key）；
3. 📊 **CRISP 对比区**：规划看板 Tab 展示 context_stats
   （粗暴全塞 vs CRISP 筛选字符数 + 压缩率；无数据不显示）；
4. 🧠 **双写记忆**：会话结束后同时写 MemoryStore 情景记忆（memory.json）
   与学生记忆库（students_memory.json，按姓名组织）；
5. 📋 **审批历史页**：独立 Tab 展示 harness.get_history()，支持筛选；
6. 🧑‍🎓 **学生记忆库页**：跨会话学生画像展示 + 历史学习记录回溯。

运行：``streamlit run app.py``
"""
from __future__ import annotations

import json
import os

import streamlit as st

from context_store import ContextStore, STUDENTS_MEMORY_PATH
from mcp_server import MCPServer
from harness import Harness
from agents import DiagnoseAgent, PlannerAgent, TutorAgent
from orchestrator import Orchestrator
from llm_client import create_llm_from_config
from memory import MemoryStore, record_student_session


# ---------------------------------------------------------------------------
# 页面配置（必须在最前面）
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="多Agent学习规划协作系统",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# 自定义 CSS 美化
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: 700; margin-bottom: 0.2rem; }
    .sub-header { font-size: 1.0rem; color: #666; margin-bottom: 1.5rem; }
    .agent-card {
        border: 1px solid #e0e0e0;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin-bottom: 1rem;
        background: #fafafa;
        transition: box-shadow 0.2s;
    }
    .agent-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
    .status-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 500;
    }
    .status-done { background: #d4edda; color: #155724; }
    .status-running { background: #fff3cd; color: #856404; }
    .status-pending { background: #f8d7da; color: #721c24; }
    .status-failed { background: #f8d7da; color: #721c24; }
    .status-skipped { background: #e2e3e5; color: #6c757d; }
    .arch-card {
        border-left: 4px solid #4a90d9;
        padding: 0.5rem 1rem;
        background: #f5f8ff;
        border-radius: 0 8px 8px 0;
        margin-bottom: 0.5rem;
    }
    .memory-card {
        border: 1px solid #d4edda;
        border-radius: 12px;
        padding: 1rem;
        background: #f0fff4;
        margin-bottom: 0.8rem;
    }
    .memory-card h4 { margin: 0 0 0.3rem 0; color: #2d6a4f; }
    .memory-card .meta { font-size: 0.8rem; color: #888; }
    .stat-box {
        text-align: center;
        padding: 1rem;
        background: #f8f9fa;
        border-radius: 10px;
        border: 1px solid #e9ecef;
    }
    .stat-box .num { font-size: 2rem; font-weight: 700; color: #4a90d9; }
    .stat-box .label { font-size: 0.85rem; color: #666; }
    hr { margin: 1.5rem 0; }
    .flow-node {
        text-align:center; padding:1rem;
        border:2px solid #ccc; border-radius:12px;
        transition: border-color 0.3s;
    }
    .flow-arrow {
        text-align:center; padding-top:2.5rem;
        font-size:1.5rem; color:#666;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 系统对象初始化：用 session_state 保存，避免每次交互重建
# ---------------------------------------------------------------------------
def 初始化系统() -> None:
    """首次运行时构建 ContextStore / MCPServer / Harness / Agents / Orchestrator / Memory。"""
    if "orchestrator" in st.session_state:
        return
    # LLM：config.json 的 "llm" 段齐全则用真实 API，否则回退 MockLLM
    llm = create_llm_from_config()
    store = ContextStore()
    mcp = MCPServer()
    harness = Harness(store)
    agents = {
        "诊断Agent": DiagnoseAgent(mcp, store, harness, llm=llm),
        "规划Agent": PlannerAgent(mcp, store, harness, llm=llm),
        "辅导Agent": TutorAgent(mcp, store, harness, llm=llm),
    }
    st.session_state.llm = llm
    st.session_state.store = store
    st.session_state.mcp = mcp
    st.session_state.harness = harness
    st.session_state.agents = agents
    st.session_state.orchestrator = Orchestrator(agents, store)
    # Memory：跨会话长期记忆（memory.json，运行时生成）
    st.session_state.memory = MemoryStore()
    st.session_state.回执 = None
    st.session_state.审批通知 = None
    st.session_state.已运行 = False


初始化系统()
store: ContextStore = st.session_state.store
mcp: MCPServer = st.session_state.mcp
harness: Harness = st.session_state.harness
orch: Orchestrator = st.session_state.orchestrator
memory: MemoryStore = st.session_state.memory

# ---------------------------------------------------------------------------
# 页面头部
# ---------------------------------------------------------------------------
st.markdown('<div class="main-header">🎓 多Agent学习规划协作系统</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">'
    '诊断 🔍 → 规划 📅 → 辅导 💡  |  通过 <b>MCP</b> 统一调用工具  |  '
    '<b>Harness</b> 保障敏感操作安全  |  <b>Memory</b> 跨会话学生画像'
    '</div>',
    unsafe_allow_html=True,
)
# LLM 模式提示（只显示模型名，不显示 key）
_llm = st.session_state.llm
st.caption(
    f"LLM：真实 API（{_llm.model}）" if _llm is not None
    else "LLM：MockLLM（未配置 config.json 的 llm 段）"
)
# 会话开始时若历史画像非空，提示"已加载历史记忆"（Memory 长期记忆生效的体现）
if memory.load_profile():
    st.info("🧠 已加载历史记忆：本系统将结合该同学的历史画像与最近会话摘要进行规划。")

# ---------------------------------------------------------------------------
# 侧边栏：学生信息表单 + 长期Memory提示
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🧑‍🎓 学生信息")

    def _get_student_history(name: str) -> dict | None:
        """按学生姓名从学生记忆库（students_memory.json）读取历史记录。"""
        if not name.strip():
            return None
        if os.path.exists(STUDENTS_MEMORY_PATH):
            try:
                with open(STUDENTS_MEMORY_PATH, "r", encoding="utf-8") as f:
                    memory_data = json.load(f)
                return memory_data.get(name.strip())
            except Exception:
                return None
        return None

    with st.form("profile_form"):
        姓名 = st.text_input(
            "姓名", value="小明", key="input_name",
            help="输入学生姓名，系统会自动识别是否有历史记录"
        )
        薄弱学科 = st.selectbox("薄弱学科", ["英语", "数学", "物理", "化学", "语文"])
        运动 = st.multiselect(
            "运动安排",
            ["网球（周二、周四）", "游泳（周六）", "篮球（周五）", "跑步（每天）"],
            default=["网球（周二、周四）", "游泳（周六）"],
        )
        备注 = st.text_area("备注", value="周三晚上效率低；英语单词量不足，阅读理解失分多")
        提交 = st.form_submit_button("🚀 开始规划", use_container_width=True)

    # 长期 Memory 提示（按姓名查 students_memory.json 中的历史）
    if 姓名.strip():
        history = _get_student_history(姓名.strip())
        if history:
            次数 = history.get("会话次数", 1)
            最新 = history.get("最新画像", {})
            薄弱 = 最新.get("薄弱学科", "(未知)")
            st.markdown("---")
            st.markdown(
                f'<div class="memory-card">'
                f"<h4>🧠 系统记得你</h4>"
                f"<div class='meta'>第 {次数} 次学习规划</div>"
                f"<div style='margin-top:6px; font-size:0.9rem;'>"
                f"📍 上次薄弱学科：<b>{薄弱}</b><br>"
                + "".join(
                    f'📝 {h.get("诊断摘要", "")}<br>'
                    for h in history.get("histories", [])[-2:]
                )
                + "</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown("---")
            st.info("🧠 初次见面！这次辅导后我会记住你。")

    if st.session_state.已运行:
        st.markdown("---")
        st.success("✅ 上次规划已完成，可修改信息再次规划")

    with st.expander("📖 架构说明"):
        st.markdown("""
**工作流程：**
1. 诊断Agent：RAG检索错题 → 生成诊断报告
2. 规划Agent：查日历/天气 → 制定一周计划
3. 辅导Agent：苏格拉底式引导教学

**课程术语：** MCP / Tool Calling / RAG / Context Engineering (CRISP) / Skill / Harness / Memory
""")

# ---------------------------------------------------------------------------
# 提交处理
# ---------------------------------------------------------------------------
if 提交:
    user_profile = {
        "姓名": 姓名.strip(),
        "薄弱学科": 薄弱学科,
        "运动": 运动,
        "城市": "上海",  # mock 天气数据只提供上海一周天气
        "备注": 备注,
    }
    # 把本次画像合并进长期记忆（新键覆盖旧键，其余保留）
    memory.update_profile(user_profile)
    with st.spinner("🔄 三个 Agent 协作中：诊断 → 规划 → 辅导 …"):
        try:
            orch.run_session(f"web-session-{姓名.strip()}", user_profile)
            st.session_state.已运行 = True
            st.session_state.回执 = None
            st.session_state.审批通知 = None

            # ----- 会话结束后双写记忆 -----
            # 1) MemoryStore 情景记忆（memory.json，保持与 demo.py 一致的逻辑）；
            # 2) 学生记忆库（students_memory.json，按姓名组织，喂给 Tab3 与侧边栏提示卡）。
            链 = store.snapshot().get("task_chain") or []
            诊断输出 = next(
                (t.get("output", "") for t in 链 if t.get("agent") == "诊断Agent"), ""
            )
            规划输出 = next(
                (t.get("output", "") for t in 链 if t.get("agent") == "规划Agent"), ""
            )
            摘要首行 = next((行.strip() for 行 in 诊断输出.splitlines() if 行.strip()), "（无输出）")
            memory.add_episode(f"web-session-{姓名.strip()}", 摘要首行, feedback="")
            record_student_session(
                姓名.strip(),
                user_profile,
                诊断摘要=诊断输出 or "（无输出）",   # 存全文，Tab3 学生记忆库完整展示
                计划摘要=规划输出,
            )
            st.success("✅ 规划完成！")
        except Exception as e:
            st.error(f"运行出错：{e}")

# ---------------------------------------------------------------------------
# 主界面：Tab 布局
# ---------------------------------------------------------------------------
tab1, tab2, tab3 = st.tabs(["📊 规划看板", "📋 审批历史", "🧠 学生记忆库"])

snapshot = store.snapshot()

# ========================= Tab 1: 规划看板 =========================
with tab1:
    # Agent 流程图（动态着色）
    st.markdown("### 🔄 Agent 协作流程")
    任务链 = snapshot.get("task_chain", [])

    def _agent_status(agent_name: str) -> str:
        for t in 任务链:
            if t.get("agent") == agent_name:
                return t.get("status", "pending")
        return "pending"

    def _status_color(status: str) -> str:
        return {
            "done": "#28a745",
            "running": "#ffc107",
            "failed": "#dc3545",
            "skipped": "#6c757d",
            "pending": "#ccc",
        }.get(status, "#ccc")

    def _status_cls(status: str) -> str:
        # approved/rejected 复用 done/failed 配色（审批历史 Tab 也用这个函数）
        return {
            "done": "done", "approved": "done",
            "running": "running",
            "failed": "failed", "rejected": "failed",
            "skipped": "skipped",
        }.get(status, "pending")

    def _status_label(status: str) -> str:
        return {
            "done": "✅ done",
            "running": "⏳ running",
            "failed": "❌ failed",
            "skipped": "⏭ skipped",
        }.get(status, status or "⏸ pending")

    agents_flow = ["诊断Agent", "规划Agent", "辅导Agent"]
    icons_flow = ["🔍", "📅", "💡"]

    cols = st.columns(5)
    for i, agent_name in enumerate(agents_flow):
        with cols[i * 2]:
            status = _agent_status(agent_name)
            cls = _status_cls(status)
            st.markdown(
                f'<div class="flow-node" style="border-color:{_status_color(status)};">'
                f'<div style="font-size:2rem;">{icons_flow[i]}</div>'
                f'<div style="font-weight:600;">{agent_name}</div>'
                f'<span class="status-badge status-{cls}">{_status_label(status)}</span>'
                f'<div style="font-size:0.8rem;color:#888;margin-top:4px;">'
                f'{["RAG检索→诊断报告","查天气日历→排计划","苏格拉底式引导"][i]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        if i < 2:
            with cols[i * 2 + 1]:
                st.markdown('<div class="flow-arrow">→</div>', unsafe_allow_html=True)

    if not 任务链:
        st.info("💡 请在左侧填写学生信息并点击「开始规划」")

    st.markdown("---")

    # 双层布局：左 Context Store JSON / 右 Agent 输出
    左栏, 右栏 = st.columns([1, 1.5])

    with 左栏:
        st.markdown("### 📦 共享上下文")
        # 统计卡片
        col_s1, col_s2, col_s3 = st.columns(3)
        with col_s1:
            st.markdown(
                f'<div class="stat-box"><div class="num">{len(任务链)}</div>'
                f'<div class="label">Agent</div></div>',
                unsafe_allow_html=True,
            )
        with col_s2:
            done_count = sum(1 for t in 任务链 if t.get("status") == "done")
            st.markdown(
                f'<div class="stat-box"><div class="num">{done_count}</div>'
                f'<div class="label">已完成</div></div>',
                unsafe_allow_html=True,
            )
        with col_s3:
            学生名 = snapshot.get("user_profile", {}).get("姓名", "")
            st.markdown(
                f'<div class="stat-box"><div class="num" style="font-size:1.2rem;">{学生名 or "-"}</div>'
                f'<div class="label">当前学生</div></div>',
                unsafe_allow_html=True,
            )

        st.json(snapshot)

    with 右栏:
        st.markdown("### 🤖 Agent 输出")
        if not 任务链:
            st.info("💡 尚未开始规划")
        for i, 任务 in enumerate(任务链):
            agent_name = 任务.get("agent", "")
            status = 任务.get("status", "")
            cls = _status_cls(status)
            with st.container():
                st.markdown(
                    f'<div class="agent-card">'
                    f'<div style="display:flex; justify-content:space-between; align-items:center;">'
                    f'<strong>{"🤖" if agent_name not in agents_flow else icons_flow[agents_flow.index(agent_name)]} {agent_name}</strong>'
                    f'<span class="status-badge status-{cls}">{_status_label(status)}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                # token 估算：任务条目里有 token_cost 字段才显示（防御性）
                token_cost = 任务.get("token_cost")
                if token_cost is not None:
                    st.caption(f"估算 {token_cost} tokens")
                output = 任务.get("output", "")
                if output:
                    st.markdown(output)
                extras = {k: v for k, v in 任务.items()
                          if k not in {"agent", "status", "input", "output", "extra", "token_cost"}}
                if extras:
                    with st.expander("📎 附加信息"):
                        for k, v in extras.items():
                            st.caption(f"**{k}**")
                            if isinstance(v, (dict, list)):
                                st.code(json.dumps(v, ensure_ascii=False, indent=2))
                            else:
                                st.code(str(v))
                st.markdown("</div>", unsafe_allow_html=True)

    # ------------------------------------------------------------------
    # CRISP 对比区：粗暴全塞 vs CRISP 筛选（防御性：context_stats 无数据则不显示）
    # ------------------------------------------------------------------
    # context_stats 由 Orchestrator 在会话末写入
    # （每项 {"agent","full_chars","crisp_chars","compressed"}）。
    context_stats = snapshot.get("context_stats")
    if isinstance(context_stats, list) and context_stats:
        有效项 = [
            s for s in context_stats
            if isinstance(s, dict) and "agent" in s
            and isinstance(s.get("full_chars"), (int, float))
            and isinstance(s.get("crisp_chars"), (int, float))
            and s.get("full_chars")
        ]
        if 有效项:
            st.markdown("---")
            st.markdown("### 📊 CRISP 上下文筛选对比（粗暴全塞 vs CRISP 筛选）")
            try:
                import pandas as pd

                图表数据 = pd.DataFrame(
                    {
                        "Agent": [s["agent"] for s in 有效项],
                        "粗暴全塞字符数": [s["full_chars"] for s in 有效项],
                        "CRISP筛选字符数": [s["crisp_chars"] for s in 有效项],
                    }
                ).set_index("Agent")
                st.bar_chart(图表数据)
            except ImportError:
                # 环境没有 pandas 时退化为表格展示
                st.table(有效项)
            # 压缩率按字符数计算；compressed 是 bool（诊断报告是否触发压缩），单独标注
            for s in 有效项:
                压缩率 = (1 - s["crisp_chars"] / s["full_chars"]) * 100
                压缩标记 = "（诊断报告已触发压缩）" if s.get("compressed") else ""
                st.caption(
                    f"· {s['agent']}：全塞 {s['full_chars']} 字 → CRISP {s['crisp_chars']} 字，"
                    f"压缩率 {压缩率:.1f}%{压缩标记}"
                )

    # ------------------------------------------------------------------
    # 敏感操作审批卡片
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 📧 敏感操作：发送家长周报")
    col_m1, col_m2 = st.columns([1, 2])
    with col_m1:
        if st.button("📩 请求发送家长周报邮件", use_container_width=True):
            tutor: TutorAgent = st.session_state.agents["辅导Agent"]
            first_output = (任务链[0].get("output", "") if 任务链 else "") or "本周学情良好。"
            result = tutor.request_parent_email("parent@example.com", first_output)
            if not result.get("ok"):
                st.session_state.审批通知 = "pending"
                st.rerun()
            else:
                st.info("邮件已直接发送（未被拦截）")

    pending = store.snapshot().get("pending_approval")
    if pending:
        with st.container(border=True):
            st.warning("⚠️ 有待审批的敏感操作")
            st.markdown(
                f"- **发起 Agent**：{pending.get('agent')}\n"
                f"- **工具**：`{pending.get('tool')}`\n"
                f"- **参数**：发给 {pending.get('args', {}).get('to', '')}，"
                f"主题「{pending.get('args', {}).get('subject', '')}」\n"
                f"- **理由**：{pending.get('reason', '')}"
            )
            with st.expander("📄 查看邮件全文"):
                # 完整展示邮件正文（不截断）；正文是诊断/计划摘要，Markdown 渲染更易读
                st.markdown(pending.get("args", {}).get("body", ""))

            col_ok, col_no = st.columns(2)
            with col_ok:
                if st.button("✅ 批准发送", use_container_width=True, type="primary"):
                    批准记录 = harness.approve()
                    st.session_state.审批通知 = "approved"
                    回执 = mcp.call_tool(批准记录["tool"], 批准记录["args"])
                    st.session_state.回执 = 回执
                    st.rerun()
            with col_no:
                if st.button("❌ 拒绝", use_container_width=True):
                    harness.reject(reason="用户在界面上拒绝")
                    st.session_state.审批通知 = "rejected"
                    st.session_state.回执 = {"ok": False, "result": None, "error": "已被拒绝"}
                    st.rerun()

    if st.session_state.审批通知 == "approved":
        st.success("✅ 已批准，邮件已发送！")
    elif st.session_state.审批通知 == "rejected":
        st.warning("❌ 已拒绝发送邮件")

    if st.session_state.回执:
        st.markdown("#### 📬 工具执行回执")
        st.json(st.session_state.回执)

# ========================= Tab 2: 审批历史 =========================
with tab2:
    st.markdown("### 📋 审批历史记录")

    history = harness.get_history()
    if not history:
        st.info("暂无审批记录。在「规划看板」中请求发送家长周报邮件会产生审批记录。")
    else:
        total = len(history)
        approved = sum(1 for h in history if h.get("status") == "approved")
        rejected = sum(1 for h in history if h.get("status") == "rejected")
        pending_count = sum(1 for h in history if h.get("status") == "pending")
        col_s1, col_s2, col_s3, col_s4 = st.columns(4)
        with col_s1:
            st.markdown(f'<div class="stat-box"><div class="num">{total}</div><div class="label">总审批</div></div>',
                        unsafe_allow_html=True)
        with col_s2:
            st.markdown(f'<div class="stat-box"><div class="num">{approved}</div><div class="label">✅ 已批准</div></div>',
                        unsafe_allow_html=True)
        with col_s3:
            st.markdown(f'<div class="stat-box"><div class="num">{rejected}</div><div class="label">❌ 已拒绝</div></div>',
                        unsafe_allow_html=True)
        with col_s4:
            st.markdown(f'<div class="stat-box"><div class="num">{pending_count}</div><div class="label">⏳ 待审批</div></div>',
                        unsafe_allow_html=True)

        筛选状态 = st.selectbox("筛选状态", ["全部", "approved", "rejected", "pending"], index=0)
        filtered = [h for h in history if 筛选状态 == "全部" or h.get("status") == 筛选状态]

        for record in reversed(filtered):
            status = record.get("status", "")
            emoji = {"approved": "✅", "rejected": "❌", "pending": "⏳"}.get(status, "❓")
            cls = _status_cls(status)
            with st.container(border=True):
                cols = st.columns([2, 1, 1, 1])
                with cols[0]:
                    st.markdown(f"**{record.get('id')}** — {record.get('agent')}")
                with cols[1]:
                    st.markdown(f"`{record.get('tool')}`")
                with cols[2]:
                    st.markdown(
                        f'<span class="status-badge status-{cls}">{emoji} {status}</span>',
                        unsafe_allow_html=True,
                    )
                with cols[3]:
                    st.caption(record.get("resolved_at", record.get("created_at", "")))
                with st.expander("详情"):
                    st.json(record)

    st.markdown("---")
    st.caption("💡 审批历史会自动持久化到 context_store.json，重启后仍可查看。")

# ========================= Tab 3: 学生记忆库 =========================
with tab3:
    st.markdown("### 🧠 学生记忆库（跨会话长期 Memory）")
    st.caption("系统会在每次规划结束后保存学生画像。同一学生再次使用时，会自动加载历史记录。")

    if os.path.exists(STUDENTS_MEMORY_PATH):
        try:
            with open(STUDENTS_MEMORY_PATH, "r", encoding="utf-8") as f:
                all_students = json.load(f)
        except Exception:
            all_students = {}
    else:
        all_students = {}

    if not all_students:
        st.info("📭 暂无学生记忆数据。完成一次学习规划后，数据会出现在这里。")
    else:
        st.success(f"📚 共 {len(all_students)} 名学生记忆")
        学生列表 = list(all_students.keys())
        选中学生 = st.selectbox("选择学生", 学生列表)

        if 选中学生:
            data = all_students[选中学生]
            最新画像 = data.get("最新画像", {})
            次数 = data.get("会话次数", 1)
            最近 = data.get("最近活动", "未知")
            首次 = data.get("首次使用", "未知")
            histories = data.get("histories", [])

            cols = st.columns(4)
            with cols[0]:
                st.markdown(
                    f'<div class="stat-box"><div class="num">{次数}</div>'
                    f'<div class="label">会话次数</div></div>',
                    unsafe_allow_html=True,
                )
            with cols[1]:
                薄弱 = 最新画像.get("薄弱学科", "-")
                st.markdown(
                    f'<div class="stat-box"><div class="num" style="font-size:1.2rem;">{薄弱}</div>'
                    f'<div class="label">薄弱学科</div></div>',
                    unsafe_allow_html=True,
                )
            with cols[2]:
                st.markdown(
                    f'<div class="stat-box"><div class="num" style="font-size:1.2rem;">{len(histories)}</div>'
                    f'<div class="label">历史记录</div></div>',
                    unsafe_allow_html=True,
                )
            with cols[3]:
                st.markdown(
                    f'<div class="stat-box"><div class="num" style="font-size:1rem;">{最近}</div>'
                    f'<div class="label">最近活动</div></div>',
                    unsafe_allow_html=True,
                )

            with st.expander("📋 最新学生画像", expanded=True):
                st.json(最新画像)

            st.markdown("#### 📜 历史学习记录")
            for i, h in enumerate(reversed(histories), 1):
                诊断 = h.get("诊断摘要", "").strip()
                计划 = h.get("计划摘要", "").strip()
                st.markdown(
                    f'<div style="border-left: 3px solid #4a90d9; padding-left: 1rem; margin-bottom: 0.8rem;">'
                    f'<div style="color:#666; font-size:0.85rem;">第 {次数 - i + 1} 次 · {h.get("时间", "未知")}</div>'
                    + (f"<div><b>诊断：</b>{诊断}</div>" if 诊断 else "")
                    + (f"<div><b>计划：</b>{计划}</div>" if 计划 else "")
                    + "</div>",
                    unsafe_allow_html=True,
                )

    st.markdown("---")
    if st.button("🗑️ 清空所有记忆（仅用于测试）"):
        # 同时清掉学生记忆库（students_memory.json）与 MemoryStore（memory.json）
        if os.path.exists(STUDENTS_MEMORY_PATH):
            os.remove(STUDENTS_MEMORY_PATH)
        memory.clear()
        st.rerun()
