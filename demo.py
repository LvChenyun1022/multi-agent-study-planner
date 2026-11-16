"""demo.py —— 命令行全流程演示（无需 streamlit、无需网络、无需 API Key）。

演示内容：
1. 构造示例 user_profile（英语薄弱、网球/游泳、周三晚上效率低）；
2. 实例化 ContextStore / MCPServer / Harness / 三个 Agent / Orchestrator；
3. run_session 跑完 诊断→规划→辅导，终端分步打印状态流转与各 Agent 输出；
4. 演示 Harness 审批链路：TutorAgent 请求 send_email → 进入审批 →
   approve() 批准 → 再显式调用 mcp.call_tool("send_email") 发送并打印回执；
5. 演示 Memory 跨会话记忆：画像写入长期记忆、会话摘要写入情景记忆、
   打印可注入 prompt 的记忆上下文与审批历史。

运行：``python demo.py``
"""
from __future__ import annotations

import json
import sys

from context_store import ContextStore
from mcp_server import MCPServer
from harness import Harness
from agents import DiagnoseAgent, PlannerAgent, TutorAgent
from orchestrator import Orchestrator
from llm_client import create_llm_from_config
from memory import MemoryStore

分隔线 = "=" * 60
小分隔线 = "-" * 60


def 配置终端输出() -> None:
    """在 Windows 等非 UTF-8 终端中安全输出中文和状态符号。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def 打印标题(文本: str) -> None:
    """打印带分隔线的章节标题。"""
    print(f"\n{分隔线}\n{文本}\n{分隔线}")


def main() -> None:
    配置终端输出()
    打印标题("多Agent学习规划协作系统 · 全流程演示")

    # 1. 示例学生档案：英语薄弱、爱打网球和游泳、周三晚上效率低
    user_profile = {
        "姓名": "小明",
        "年级": "高一",
        "薄弱学科": "英语",
        "运动": ["网球（周二、周四）", "游泳（周六）"],
        "城市": "上海",  # mock 天气数据只提供上海一周天气
        "备注": "周三晚上效率低；英语单词量不足，阅读理解失分多",
    }
    print("学生档案：")
    print(json.dumps(user_profile, ensure_ascii=False, indent=2))

    # Memory：跨会话长期记忆（memory.json，运行时生成，不入库）
    memory = MemoryStore()
    # 把学生画像写入长期记忆（合并式更新，旧画像信息不会被冲掉）
    memory.update_profile(user_profile)

    # 2. 组装系统各层：上下文 → 工具 → 安全 → Agent → 调度器
    # LLM：config.json 的 "llm" 段齐全则用真实 API，否则（或 API 失败时）回退 MockLLM
    llm = create_llm_from_config()
    if llm is not None:
        print(f"\nLLM 模式：真实 API（模型 {llm.model}，base_url {llm.base_url}）")
    else:
        print("\nLLM 模式：MockLLM（未在 config.json 中配置 llm 段）")
    store = ContextStore()
    mcp = MCPServer()
    harness = Harness(store)
    agents = {
        "诊断Agent": DiagnoseAgent(mcp, store, harness, llm=llm),
        "规划Agent": PlannerAgent(mcp, store, harness, llm=llm),
        "辅导Agent": TutorAgent(mcp, store, harness, llm=llm),
    }
    orch = Orchestrator(agents, store)

    # 3. 跑一次完整会话
    打印标题("第一阶段：Orchestrator 调度三个 Agent（诊断 → 规划 → 辅导）")
    snapshot = orch.run_session("demo-session-001", user_profile)

    # 会话结束后：把本次摘要写入情景记忆（取诊断结论首行作为摘要）
    诊断输出 = snapshot.get("task_chain", [{}])[0].get("output", "")
    摘要首行 = next((行 for 行 in 诊断输出.splitlines() if 行.strip()), "（无输出）")
    memory.add_episode("demo-session-001", 摘要首行.strip(), feedback="")

    # 4. 分步打印每个 Agent 的状态与输出
    打印标题("第二阶段：各 Agent 状态流转与输出明细")
    # 任务条目的基础键；其余键（rag_sources/tool_calls/skill_used 等）都视为附加信息
    基础键 = {"agent", "status", "input", "input_note", "output", "extra"}
    for 任务 in snapshot.get("task_chain", []):
        print(f"\n{小分隔线}")
        print(f"Agent ：{任务.get('agent')}")
        print(f"状态  ：{任务.get('status')}")
        附加 = dict(任务.get("extra") or {})
        附加.update({k: v for k, v in 任务.items() if k not in 基础键})
        for k, v in 附加.items():
            print(f"附加  ：{k} = {v}")
        print(f"输出  ：\n{任务.get('output', '')}")

    # 5. 演示 Harness 审批链路（敏感工具 send_email）
    打印标题("第三阶段：Harness 审批链路演示（给家长发周报邮件）")
    tutor = agents["辅导Agent"]
    摘要 = snapshot.get("task_chain", [{}])[0].get("output", "")[:200] or "本周学情良好。"
    结果 = tutor.request_parent_email("parent@example.com", 摘要)

    if not 结果.get("ok"):
        记录 = 结果["pending_approval"]
        print("⚠ send_email 属于敏感工具，已进入审批队列：")
        print(json.dumps(记录, ensure_ascii=False, indent=2))
        print(f"\n当前 Context Store 中的 pending_approval：")
        print(json.dumps(store.snapshot().get("pending_approval"), ensure_ascii=False, indent=2))

        print(f"\n{小分隔线}\n模拟人工操作：批准该申请 …\n{小分隔线}")
        批准记录 = harness.approve()
        print(f"审批结果：{批准记录.get('status')}（审批单号 {批准记录.get('id')}）")

        # 批准后，显式调用 MCP 真正执行 send_email，打印回执
        回执 = mcp.call_tool("send_email", 批准记录["args"])
        print("邮件发送回执：")
        print(json.dumps(回执, ensure_ascii=False, indent=2))
    else:
        print("（意外：send_email 未被 Harness 拦截，请检查 harness.py）")
        print(json.dumps(结果, ensure_ascii=False, indent=2))

    # 6. Memory 跨会话演示：长期画像 + 情景记忆 + 审批历史
    打印标题("第四阶段：Memory 跨会话演示（长期记忆 + 情景记忆）")
    print("长期记忆 · 用户画像（memory.json → profile）：")
    print(json.dumps(memory.load_profile(), ensure_ascii=False, indent=2))
    print(f"\n情景记忆 · 最近会话摘要（共 {len(memory.get_episodes(limit=99))} 条，示最近 5 条）：")
    for e in memory.get_episodes(limit=5):
        print(f"  · [{e.get('timestamp')}] {e.get('session_id')}：{e.get('summary')}")
    print(f"\n{小分隔线}\n可注入 prompt 的记忆上下文（build_memory_context，≤300字）：")
    print(memory.build_memory_context())
    print("\n👉 下次启动新会话时这段记忆会被注入，Agent 不会从头问起。")

    # 审批历史：第三阶段批准后应有 1 条
    审批历史 = store.snapshot().get("approval_history") or []
    print(f"\n{小分隔线}\nHarness 审批历史（共 {len(审批历史)} 条）：")
    for h in 审批历史:
        print(
            f"  · [{h.get('timestamp')}] {h.get('agent')} 请求 {h.get('tool')} "
            f"→ {h.get('decision')}（单号 {h.get('id')}）"
        )
    if not 审批历史:
        print("  （暂无审批记录）")

    打印标题("演示结束：Context Store 最终快照")
    print(json.dumps(store.snapshot(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
