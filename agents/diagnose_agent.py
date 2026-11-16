"""诊断 Agent：检索错题 → 生成学情诊断报告。

工作流程（对应课程术语 RAG + Prompt Engineering）：
1. 从上下文（CRISP 筛选后的 user_profile）中取出薄弱学科与备注；
2. 构造检索 query：薄弱学科 + 备注中命中的学习关键词 + 学科扩展词，
   调用 MCP 工具 ``rag_search`` 检索错题本/教材语料；
3. **先把检索到的 dict 条目格式化成可读中文**（【来源】内容摘要），
   再拼进 prompt 交给 LLM 生成诊断报告——不把原始 dict 塞给模型；
4. 返回 {"output": 报告文本, "rag_sources": [来源字符串列表]}。
"""
from __future__ import annotations

from .base_agent import BaseAgent

# 备注里常见的学习类关键词：用于从学生备注中提取检索线索
备注关键词表 = ["语法", "时态", "单词", "阅读", "听力", "口语", "写作", "计算", "概念", "公式"]

# 各薄弱学科的扩展检索词：备注信息不足时也能保证 query 有区分度
学科扩展词 = {
    "英语": ["过去时", "现在完成时", "时态"],
    "数学": ["有理数", "负数", "乘除"],
    "语文": ["阅读", "作文"],
    "物理": ["受力", "电路"],
    "化学": ["方程式", "元素"],
}


class DiagnoseAgent(BaseAgent):
    """诊断 Agent：通过 RAG 检索定位薄弱点并生成诊断报告。"""

    name = "诊断Agent"

    def run(self, context: dict) -> dict:
        """执行诊断。

        context 由 Orchestrator.select_context 提供，只包含 user_profile
        （CRISP 原则：诊断阶段不需要看到其他 Agent 的中间结果）。
        """
        user_profile: dict = context.get("user_profile", {})
        薄弱学科 = user_profile.get("薄弱学科", user_profile.get("weak_subject", "未知学科"))
        备注 = user_profile.get("备注", user_profile.get("note", ""))

        # 第一步：构造 query 并做 RAG 检索。
        # query = 薄弱学科 + 备注命中的学习关键词 + 学科扩展词，
        # 例如"英语 阅读 过去时 现在完成时 时态"，一般能命中 2~3 条相关语料。
        备注命中词 = [词 for 词 in 备注关键词表 if 词 in 备注]
        query = " ".join(
            [薄弱学科, *备注命中词, *学科扩展词.get(薄弱学科, [])]
        )
        检索结果 = self.call_tool("rag_search", {"query": query, "top_k": 3})

        # 第二步：把检索到的 dict 条目格式化成可读中文（关键！不把原始 dict 传给 LLM）
        条目文本们: list[str] = []
        来源列表: list[str] = []
        if 检索结果.get("ok"):
            for 条 in 检索结果.get("result") or []:
                if isinstance(条, dict):
                    来源 = str(条.get("source", "未知来源"))
                    摘要 = self._内容摘要(str(条.get("content", "")))
                else:  # 兼容直接返回字符串的实现
                    来源, 摘要 = "检索结果", self._内容摘要(str(条))
                条目文本们.append(f"【{来源}】{摘要}")
                来源列表.append(来源)

        # 第三步：拼 prompt，请 LLM 生成诊断报告（错题证据逐条列出：来源+摘要）
        system = (
            "你是一位资深学习诊断专家，请根据学生档案与错题检索材料，"
            "输出结构化的诊断报告，指出薄弱点并给出归因。"
        )
        user = (
            f"学生薄弱学科：{薄弱学科}\n"
            f"备注：{备注 or '无'}\n"
            "RAG 检索到的错题/教材条目：\n"
            + ("\n".join(f"- {条}" for 条 in 条目文本们) if 条目文本们 else "（检索为空）")
        )
        报告 = self.llm.chat(system, user)

        return {"output": 报告, "rag_sources": 来源列表}

    @staticmethod
    def _内容摘要(内容: str, 上限: int = 60) -> str:
        """把长文本截成一行摘要，避免把整段语料塞进 prompt。"""
        单行 = " ".join(内容.split())
        return 单行 if len(单行) <= 上限 else 单行[:上限] + "…"
