"""辅导 Agent：加载 Skill（苏格拉底教学 SOP）→ 引导式提问。

对应课程术语 Skill / Context Engineering：
- Skill 就是一个写给人和模型一起看的 Markdown SOP 文件
  （``skills/skill_tutor.md``），运行时被**原文塞入 system prompt**，
  让模型"照章办事"；
- 本 Agent 还演示了敏感工具链路：``request_parent_email`` 调用
  ``send_email``（敏感工具），会被 Harness 拦下进入审批队列。
"""
from __future__ import annotations

import re

from .base_agent import BaseAgent

# Skill 文件路径（相对项目根目录运行）
SKILL_PATH = "skills/skill_tutor.md"


class TutorAgent(BaseAgent):
    """辅导 Agent：按苏格拉底 SOP 对薄弱点进行引导式提问（永不直接给答案）。"""

    name = "辅导Agent"

    def _加载skill(self) -> str:
        """读取 skill_tutor.md 原文；文件缺失时返回兜底提示而不是崩溃。"""
        try:
            with open(SKILL_PATH, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return (
                "（警告：未找到 skills/skill_tutor.md，使用内置兜底原则："
                "永不直接给答案；用提问引导学生自己发现解法。）"
            )

    def run(self, context: dict) -> dict:
        """执行辅导引导。

        context 由 Orchestrator.select_context 提供（CRISP）：
        诊断 output + 计划 output，不含全量 task_chain。
        """
        skill原文 = self._加载skill()
        诊断报告 = context.get("诊断Agent_output", "")
        计划 = context.get("规划Agent_output", "")

        # 先从诊断报告中提取**一两行简短薄弱点摘要**（如"英语：基础概念不牢 + 练习量不足"），
        # 而不是把整段诊断报告回显给模型——避免输出出现层层嵌套的 bullet 垃圾。
        薄弱点摘要 = self._提取薄弱点摘要(诊断报告)

        # 把 Skill 原文放进 system prompt —— 这是 Skill 机制的核心用法
        system = (
            "你是一位严格遵循以下教学 SOP 的辅导老师。\n"
            "===== Skill SOP 开始 =====\n"
            f"{skill原文}\n"
            "===== Skill SOP 结束 =====\n"
            "请针对学生的薄弱点，生成第一轮苏格拉底式引导提问。"
        )
        user = (
            f"学生薄弱点：{薄弱点摘要}\n"
            f"学习计划要点：{self._提取计划要点(计划)}"
        )
        引导 = self.llm.chat(system, user)
        return {"output": 引导, "skill_used": "skill_tutor.md"}

    # ------------------------------------------------------------------
    # 内部辅助：从上游长文本中提炼简短摘要
    # ------------------------------------------------------------------
    @staticmethod
    def _提取薄弱点摘要(诊断报告: str) -> str:
        """从诊断报告中提取"学科：归因"式的一行摘要。

        优先匹配「诊断结论」行中「…」里的归因短语，再拼上薄弱学科；
        提取失败时退化为诊断报告的第一行（截断），绝不整段回显。
        """
        学科 = ""
        归因 = ""
        for 行 in 诊断报告.splitlines():
            if "薄弱学科" in 行 and "：" in 行:
                学科 = 行.split("：", 1)[1].strip()
            if "诊断结论" in 行:
                m = re.search(r"「([^」]+)」", 行)
                if m:
                    归因 = m.group(1).strip()
        if 学科 and 归因:
            return f"{学科}：{归因}"
        if 学科:
            return 学科
        首行 = 诊断报告.strip().splitlines()[0] if 诊断报告.strip() else ""
        return (首行[:40] + "…") if len(首行) > 40 else (首行 or "（暂无诊断结论）")

    @staticmethod
    def _提取计划要点(计划: str, 行数: int = 3) -> str:
        """从计划文本中取前几行非空要点（截断），避免整表回显。"""
        行们 = [行.strip() for 行 in 计划.splitlines() if 行.strip()]
        if not 行们:
            return "（暂无计划）"
        return "；".join(行们[:行数])[:120]

    def request_parent_email(self, 家长邮箱: str, 摘要: str) -> dict:
        """演示敏感工具审批链路：请求给家长发送学情周报邮件。

        ``send_email`` 在 tool_schemas 中被标记为 sensitive=true，
        因此 ``call_tool`` 不会真正发送，而是经 Harness 生成一条
        pending_approval 记录并返回。人工批准后，再由调用方
        （demo.py / app.py）显式调用 ``mcp.call_tool("send_email", ...)``
        完成发送并拿到回执。
        """
        args = {
            "to": 家长邮箱,
            "subject": "本周学情周报",
            "body": f"家长您好，以下是孩子本周的学习诊断与计划摘要：\n{摘要}",
        }
        return self.call_tool("send_email", args)
