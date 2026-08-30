# -*- coding: utf-8 -*-
"""
harness.py — Harness：敏感操作审批队列 + 审批历史记录

Harness（"挽具/安全带"）思想：Agent 可以自主调用工具，但遇到"敏感操作"
（如发邮件、删数据）时必须先暂停，等待人类审批通过后才能执行。

工作流程：
1. Agent 想调用敏感工具 → request_approval() 生成一条 pending 审批记录，
   写入 ContextStore（这样前端 app.py 也能看到并展示审批卡片）；
2. 人类查看后调用 approve() 或 reject()；
3. approve() 后 Agent 才真正执行工具调用。

本模块只维护"审批队列"，不执行工具——执行交给 MCPServer。

审批历史（audit trail）：approve()/reject() 会通过
``ContextStore.add_approval_record()`` 把本次审批的完整记录追加到
ContextStore 顶层键 "approval_history"（list），字段包括
{"id","agent","tool","args","reason","status","created_at",
"resolved_at","resolved_by"}；为满足旧版 demo.py 第四阶段的读取习惯，
每条记录同时冗余写入 "decision"（与 status 同值）和
"timestamp"（与 resolved_at 同值）两个兼容字段。
前端"审批历史"Tab 通过 get_history() 读取。
"""

import datetime
import uuid

from context_store import ContextStore

# 敏感工具名单：必须与 tool_schemas.json 中 sensitive=true 的工具保持一致
SENSITIVE_TOOLS = {"send_email"}


def _short_id() -> str:
    """生成 uuid4 短码（取前8位），作为审批记录的 id。"""
    return uuid.uuid4().hex[:8]


def _now_str() -> str:
    """返回当前时间字符串（"%Y-%m-%d %H:%M:%S"）。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Harness:
    """审批队列：拦截敏感工具调用，等待人类批准/拒绝，自动记录审批历史。"""

    def __init__(self, store: ContextStore) -> None:
        # Harness 依赖 ContextStore 来持久化待审批记录与审批历史（前端也要读）
        self.store = store

    def is_sensitive(self, tool_name: str) -> bool:
        """判断某工具是否属于敏感操作。"""
        return tool_name in SENSITIVE_TOOLS

    def request_approval(self, agent: str, tool_name: str, args: dict) -> dict:
        """生成一条审批记录并写入 ContextStore，状态为 pending（等待审批）。

        返回的记录格式：
        {"id": 短码, "agent": 发起者, "tool": 工具名, "args": 调用参数,
         "reason": 原因说明, "status": "pending", "created_at": 发起时间}
        """
        approval = {
            "id": _short_id(),
            "agent": agent,
            "tool": tool_name,
            "args": args,
            "reason": f"{agent} 请求执行敏感操作 {tool_name}，等待人工审批",
            "status": "pending",
            "created_at": _now_str(),
        }
        self.store.set_pending_approval(approval)
        return approval

    def _resolve(self, approval: dict, status: str) -> None:
        """审批结束时补齐历史字段并写入 approval_history。

        status 为 "approved" / "rejected"；同时写入兼容字段：
        - "decision"：与 status 同值（旧版 demo.py 第四阶段读取该字段）；
        - "timestamp"：与 resolved_at 同值（旧版展示代码读取该字段）。
        """
        now = _now_str()
        approval["status"] = status
        approval["decision"] = status      # 兼容旧代码（demo.py 审批摘要）
        approval["resolved_at"] = now
        approval["resolved_by"] = "人工审批"
        approval["timestamp"] = now        # 兼容旧代码（demo.py 审批摘要）
        self.store.add_approval_record(approval)
        self.store.set_pending_approval(None)  # 清空待审批

    def approve(self) -> dict:
        """批准当前待审批记录：状态改为 approved，记入历史，清空 pending。

        没有待审批记录时返回空 dict（防御性处理）。
        """
        approval = self.store.get("pending_approval")
        if not approval:
            return {}
        self._resolve(approval, "approved")
        return approval

    def reject(self, reason: str = "") -> dict:
        """拒绝当前待审批记录：状态改为 rejected，附原因，记入历史，清空 pending。

        没有待审批记录时返回空 dict（防御性处理）。
        """
        approval = self.store.get("pending_approval")
        if not approval:
            return {}
        if reason:
            approval["reason"] = reason
        self._resolve(approval, "rejected")
        return approval

    def get_history(self, limit: int = 50) -> list[dict]:
        """查询审批历史（最近 limit 条），供前端"审批历史"Tab 展示。"""
        history = self.store.get_approval_history()
        return history[-limit:]
