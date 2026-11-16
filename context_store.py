# -*- coding: utf-8 -*-
"""
context_store.py — ContextStore：多Agent共享上下文仓库

作用：把一次"学习规划会话"中的关键信息（会话id、学生画像、各Agent任务链、
待审批记录）集中存到一个 JSON 文件里，让所有 Agent 读写同一份"黑板"。

设计要点（给大一同学）：
- 内存里维护一份 dict（self.state），所有读写都先操作内存；
- load() 从磁盘读入内存；save() 把内存写回磁盘（utf-8、indent=2、不转义中文）；
- start_task / finish_task 维护 task_chain（任务链），体现 Memory 思想：
  每个 Agent 的输入输出都会留下痕迹，供后续 Agent 和前端展示使用。
"""

import copy
import json
import os
import threading

# 按学生姓名组织的长期记忆文件（memory.py 的 record_student_session 负责写入，
# 前端 app.py 的"学生记忆库"Tab 与侧边栏 Memory 提示卡负责读取）。
# 运行时生成文件，已加入 .gitignore。
STUDENTS_MEMORY_PATH = "students_memory.json"


class ContextStore:
    """共享上下文仓库：负责 context_store.json 的读写与任务链维护。

    线程安全：内部持有一把 ``threading.RLock``（可重入锁），所有读写方法
    都在锁内执行——Orchestrator 的 DAG 并行调度（task_plan + ThreadPoolExecutor）
    会让多个 Agent 线程并发调用 start_task/finish_task/update_task/set/save/
    snapshot，RLock 保证 task_chain 追加/更新与 JSON 落盘不会出现竞态。
    公开接口完全不变，串行调用方无感。
    """

    def __init__(self, path: str = "context_store.json") -> None:
        # JSON 文件路径（默认当前目录下的 context_store.json）
        self.path = path
        # 可重入锁：start_task 等方法内部会再调 save()，必须可重入
        self._lock = threading.RLock()
        # 内存中的状态，先用空模板占位，之后通过 load()/reset() 填充
        self.state: dict = {
            "session_id": "",
            "user_profile": {},
            "task_chain": [],
            "pending_approval": None,
            "context_stats": [],  # CRISP 上下文统计（Orchestrator 在会话末写入）
        }

    def load(self) -> dict:
        """从 JSON 文件读入内存并返回。

        文件不存在时保留当前内存状态（通常是初始模板），不报错，
        方便第一次运行时直接使用。
        """
        with self._lock:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            return self.state

    def save(self) -> None:
        """把内存中的 state 写回 JSON 文件。

        约定：utf-8 编码、indent=2 方便人工查看、ensure_ascii=False 保留中文。
        """
        with self._lock:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)

    def reset(self, session_id: str, user_profile: dict) -> None:
        """初始化一次新会话：清空任务链和待审批记录，写入会话id与学生画像。"""
        with self._lock:
            self.state = {
                "session_id": session_id,
                "user_profile": user_profile,
                "task_chain": [],
                "pending_approval": None,
                "context_stats": [],
            }
            self.save()

    def get(self, key: str, default=None):
        """读取顶层字段（如 "task_chain"、"user_profile"），不存在时返回 default。"""
        with self._lock:
            return self.state.get(key, default)

    def set(self, key: str, value) -> None:
        """写入顶层字段并保存（如 Orchestrator 写入 "context_stats"）。

        与 get() 对应的通用写接口；新增键不影响既有字段，向后兼容。
        """
        with self._lock:
            self.state[key] = value
            self.save()

    def start_task(self, agent: str, input_note: str = "") -> None:
        """标记某个 Agent 的任务开始（status=running）。

        若该 agent 已在 task_chain 中（例如重试），则更新原条目为 running；
        否则在任务链末尾追加一条新记录。
        """
        with self._lock:
            for task in self.state["task_chain"]:
                if task.get("agent") == agent:
                    task["status"] = "running"
                    task["input"] = input_note
                    self.save()
                    return
            self.state["task_chain"].append(
                {"agent": agent, "status": "running", "input": input_note}
            )
            self.save()

    def finish_task(self, agent: str, output: str, extra: dict | None = None) -> None:
        """标记某个 Agent 的任务完成：写入 status=done 和输出结果。

        extra 里可以放 Agent 的附加返回值（比如 rag_sources、tool_calls），
        这些键会被合并进该任务条目，供前端或其他 Agent 查看。
        """
        with self._lock:
            for task in self.state["task_chain"]:
                if task.get("agent") == agent:
                    task["status"] = "done"
                    task["output"] = output
                    if extra:
                        task.update(extra)
                    self.save()
                    return
            # 防御：没有 start 就直接 finish 时，也补一条完整记录
            record = {"agent": agent, "status": "done", "input": "", "output": output}
            if extra:
                record.update(extra)
            self.state["task_chain"].append(record)
            self.save()

    def update_task(self, agent: str, status: str, **fields) -> None:
        """通用任务状态更新（供 Orchestrator 标记 failed / skipped 等扩展终态）。

        与 start_task/finish_task 互补：那两个方法只管 running/done 主流程，
        本方法支持任意状态值与附加字段（如 error、skip_reason、token_cost）。
        """
        with self._lock:
            for task in self.state["task_chain"]:
                if task.get("agent") == agent:
                    task["status"] = status
                    task.update(fields)
                    self.save()
                    return
            # 防御：没有 start 就直接 update 时，也补一条完整记录
            record = {"agent": agent, "status": status, "input": ""}
            record.update(fields)
            self.state["task_chain"].append(record)
            self.save()

    def set_pending_approval(self, approval: dict | None) -> None:
        """设置/清空当前待审批记录（Harness 审批队列使用）。"""
        with self._lock:
            self.state["pending_approval"] = approval
            self.save()

    def add_approval_record(self, record: dict) -> None:
        """把一条审批决定追加到顶层 "approval_history" 列表并落盘（加锁）。

        供 Harness.approve()/reject() 在审批结束时做审计留痕；
        顶层键不在初始模板里，首次追加时自动创建。
        """
        with self._lock:
            history = self.state.get("approval_history")
            if not isinstance(history, list):
                history = []
            history.append(record)
            self.state["approval_history"] = history
            self.save()

    def get_approval_history(self) -> list:
        """读取审批历史列表；无该键（或类型异常）时返回空列表。"""
        with self._lock:
            history = self.state.get("approval_history")
            return list(history) if isinstance(history, list) else []

    def snapshot(self) -> dict:
        """返回当前内存 state 的深拷贝，防止调用方意外修改内部数据。"""
        with self._lock:
            return copy.deepcopy(self.state)
