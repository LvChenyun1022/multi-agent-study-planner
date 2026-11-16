
# -*- coding: utf-8 -*-
"""demo_parallel.py —— 第二阶段演示:并行调度 + 更多 Agent + 失败重试 + 条件分支。

运行:``python demo_parallel.py``(无需网络、无需 API Key)
"""
from __future__ import annotations

import json
import sys
import time

from context_store import ContextStore
from mcp_server import MCPServer
from harness import Harness
from agents import BaseAgent, DiagnoseAgent, PlannerAgent, TutorAgent
from orchestrator import Orchestrator

分隔线 = "=" * 60
小分隔线 = "-" * 60


def 配置终端输出() -> None:
    """在 Windows 等非 UTF-8 终端中安全输出中文和状态符号。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def 打印标题(文本: str) -> None:
    print(f"\n{分隔线}\n{文本}\n{分隔线}")


class 学情摘要Agent(BaseAgent):
    """新 Agent 示例:从学生档案生成一行学情摘要,与诊断 Agent 并行执行。

    说明:Orchestrator.select_context 只认识三个内置 Agent,对未知 Agent 返回空
    上下文;这里演示「更多 Agent」的接入方式——Agent 直接通过 self.store 读取
    自己所需的共享上下文(只取 user_profile,符合 CRISP:只拿必要的)。
    """

    name = "学情摘要Agent"

    def run(self, context: dict) -> dict:
        time.sleep(0.3)  # 模拟耗时,便于在日志里观察并行
        profile = self.store.snapshot().get("user_profile", {})
        姓名 = profile.get("姓名", "学生")
        学科 = profile.get("薄弱学科", "未知学科")
        运动 = "、".join(profile.get("运动", []) or []) or "无"
        output = f"【学情摘要】{姓名}:薄弱学科为{学科};运动安排:{运动}。"
        return {"output": output, "skill_used": "summary"}


class 不稳定Agent(BaseAgent):
    """演示失败重试:第一次运行抛异常,第二次成功。"""

    name = "不稳定Agent"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._attempts = 0

    def run(self, context: dict) -> dict:
        self._attempts += 1
        if self._attempts == 1:
            raise RuntimeError("第一次运行失败(演示重试机制)")
        time.sleep(0.2)
        return {
            "output": f"【不稳定Agent】第 {self._attempts} 次尝试成功。",
            "attempts": self._attempts,
        }


class 竞赛拔高Agent(BaseAgent):
    """演示条件分支:仅当学生档案里出现「竞赛」字样时才运行(本 demo 会被跳过)。"""

    name = "竞赛拔高Agent"

    def run(self, context: dict) -> dict:
        return {"output": "【竞赛拔高】安排竞赛专题训练。"}


def 需要竞赛拔高(outputs: dict, store: ContextStore) -> bool:
    """节点 condition:判断是否需要竞赛拔高(本 demo 返回 False,演示跳过)。"""
    profile = store.snapshot().get("user_profile", {})
    return "竞赛" in json.dumps(profile, ensure_ascii=False)


def main() -> None:
    配置终端输出()
    打印标题("第二阶段演示:并行调度 + 更多 Agent + 失败重试 + 条件分支")

    user_profile = {
        "姓名": "小明",
        "年级": "高一",
        "薄弱学科": "英语",
        "运动": ["网球(周二、周四)", "游泳(周六)"],
        "城市": "上海",
        "备注": "周三晚上效率低;英语单词量不足,阅读理解失分多",
    }

    # 用单独文件,避免覆盖原 demo 的 context_store.json
    store = ContextStore("context_store_parallel.json")
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

    # 自定义任务计划(DAG):三个无依赖节点第一波并行;竞赛拔高被 condition 跳过
    计划 = [
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

    orch = Orchestrator(agents, store, task_plan=计划, max_workers=3)
    snapshot = orch.run_session("demo-parallel-001", user_profile)

    打印标题("任务链最终状态")
    基础键 = {"agent", "status", "input", "input_note", "output", "extra"}
    for 任务 in snapshot.get("task_chain", []):
        print(f"\n{小分隔线}")
        print(f"Agent :{任务.get('agent')}")
        print(f"状态  :{任务.get('status')}")
        附加 = dict(任务.get("extra") or {})
        附加.update({k: v for k, v in 任务.items() if k not in 基础键})
        for k, v in 附加.items():
            print(f"附加  :{k} = {v}")
        print(f"输出  :\n{任务.get('output', '')}")

    打印标题("演示结束")


if __name__ == "__main__":
    main()
