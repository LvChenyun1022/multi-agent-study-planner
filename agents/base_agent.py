"""Agent 基类与 LLM 抽象层。

本模块定义了三个核心概念：

1. ``LLMClient`` —— 一个 ``typing.Protocol``，规定任何"大模型客户端"
   只要实现了 ``chat(system, user) -> str`` 就能被 Agent 使用。
   这体现了课程中的"接口与实现分离"思想（依赖注入）。

2. ``MockLLM`` —— 默认的离线 LLM 实现。它不调用任何真实模型，
   而是根据 system prompt 中的关键词（诊断 / 规划 / 辅导）选择一套
   中文规则模板，把 user 输入里的关键信息"摘"进模板里返回。
   好处：无网络、无 API Key 也能跑通全流程，方便课堂演示与单元测试。

3. ``BaseAgent`` —— 所有业务 Agent 的基类，统一持有
   ``mcp``（工具层）、``store``（共享上下文）、``harness``（安全层）、
   ``llm``（模型层）四个依赖，并提供统一的 ``call_tool`` 入口：
   **任何工具调用都必须先经过 Harness 检查**，敏感工具会被拦下进入审批队列。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# LLM 抽象：Protocol 定义"长得像 LLM 就行"
# ---------------------------------------------------------------------------
@runtime_checkable
class LLMClient(Protocol):
    """LLM 客户端协议：只要实现 chat 方法即可作为 LLM 注入 Agent。"""

    def chat(self, system: str, user: str) -> str:
        """输入 system（角色设定）与 user（用户问题/材料），输出一段文本回复。"""
        ...  # pragma: no cover - 协议方法无实现


class MockLLM:
    """离线 Mock LLM：基于关键词的规则模板生成器。

    工作原理（朴素但够用）：
    - 在 system prompt 里找关键词，判断当前是哪个 Agent 在说话；
    - 选中对应的模板，把 user 输入里的关键行（学科/薄弱点/运动等）
      拼进模板，让回复看起来"引用"了输入内容。

    如何替换为真实 LLM（以 OpenAI 兼容接口为例）::

        import openai

        class OpenAILLM:
            def __init__(self, model: str = "gpt-4o-mini"):
                self.client = openai.OpenAI()  # 从环境变量读 API Key
                self.model = model

            def chat(self, system: str, user: str) -> str:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                return resp.choices[0].message.content

        # 注入到任意 Agent：
        agent = DiagnoseAgent(mcp, store, harness, llm=OpenAILLM())

    其他任何兼容 ``chat(system, user) -> str`` 的客户端（如国产模型
    的 SDK、本地 Ollama 等）都可以按同样方式注入，无需改动 Agent 代码。
    """

    def chat(self, system: str, user: str) -> str:
        """根据 system 关键词选择模板，并把 user 的关键信息嵌入回复。"""
        if "诊断" in system:
            return self._诊断模板(user)
        if "规划" in system:
            return self._规划模板(user)
        if "辅导" in system:
            return self._辅导模板(user)
        # 兜底：未知角色时原样回显，方便调试
        return f"【MockLLM 兜底回复】收到材料：\n{user}"

    # ------------------------------------------------------------------
    # 三套角色模板（内部方法，仅本类使用）
    # ------------------------------------------------------------------
    def _诊断模板(self, user: str) -> str:
        """诊断 Agent 模板：指出薄弱点，引用检索到的错题材料。"""
        return (
            "【诊断报告】\n"
            "一、学情概述：根据学生档案与错题检索结果，学生当前的主要薄弱点集中在：\n"
            f"{self._摘行(user, ('薄弱', '学科', '数学', '英语', '物理', '化学', '语文')) or '（未在材料中找到明确薄弱学科）'}\n"
            "二、错题证据：\n"
            f"{self._摘行(user, ('错题', '检索', 'RAG', '来源')) or '（检索结果为空，建议补充错题本数据）'}\n"
            "三、诊断结论：该生属于「基础概念不牢 + 练习量不足」型，"
            "建议先回归课本概念，再用同类题巩固。\n"
            "（本报告由 MockLLM 规则模板生成，接入真实 LLM 后可获得更细致的分析。）"
        )

    def _规划模板(self, user: str) -> str:
        """规划 Agent 模板：强调结合日历/天气与运动安排。"""
        return (
            "【规划思路】\n"
            "综合一周日历、天气预报与学生运动习惯，排课原则如下：\n"
            f"{self._摘行(user, ('运动', '网球', '游泳', '周三', '效率')) or '（材料中未提及运动/效率信息）'}\n"
            "1. 运动日安排轻量复习，避免高强度新内容；\n"
            "2. 周三晚上效率低，只排 30 分钟以内的回顾任务；\n"
            "3. 天气晴好的下午优先安排户外/运动，室内学习放在早晚；\n"
            "4. 每天聚焦 1~2 个知识点，宁少勿滥。\n"
            "（本思路由 MockLLM 规则模板生成。）"
        )

    def _辅导模板(self, user: str) -> str:
        """辅导 Agent 模板：苏格拉底式提问，绝不直接给答案。"""
        return (
            "【苏格拉底式引导】\n"
            "我不会直接告诉你答案，我们一步一步来：\n"
            f"针对你的薄弱点——{self._摘行(user, ('薄弱', '诊断', '概念')) or '（请先阅读诊断报告）'}\n"
            "问题 1：看到这道题，你第一反应它考的是哪个知识点？能用自己的话说说吗？\n"
            "问题 2：如果把题目里的条件换一下，你觉得解法会变吗？为什么？\n"
            "问题 3：你之前做错的这一步，当时是怎么想的？我们再从那里出发一次。\n"
            "（请尝试回答其中任意一问，我会根据你的回答决定下一步："
            "答对就加深一层，连续错两次我会降低难度并给出提示。）"
        )

    @staticmethod
    def _摘行(text: str, 关键词们: tuple[str, ...]) -> str:
        """从多行文本中挑出包含任一关键词的行，拼成摘要；用于让回复"引用"输入。

        鲁棒性处理：
        - 剥掉行首已有的 bullet 符号（-、·、•、*），避免输出出现 "· ·" 嵌套；
        - 丢弃字典型脏行（以 "{" 开头、或含 repr 特征的 "':"），
          防止工具返回的原始 dict 被回显到最终回复里。
        """
        命中 = []
        for 原始行 in text.splitlines():
            行 = 原始行.strip().lstrip("-·•*").strip()
            if not 行:
                continue
            if 行.startswith("{") or 行.startswith("[") or "':" in 行 or '":' in 行:
                continue  # 疑似 dict/list 的 repr，跳过
            if any(k in 行 for k in 关键词们):
                命中.append(行)
        if not 命中:
            return ""
        return "\n".join(f"  · {行}" for 行 in 命中[:6])


# ---------------------------------------------------------------------------
# Agent 基类
# ---------------------------------------------------------------------------
class BaseAgent:
    """所有业务 Agent 的基类。

    子类只需做两件事：
    1. 设置类属性 ``name``（如 "诊断Agent"）；
    2. 实现 ``run(context) -> dict``，返回至少包含 ``"output"`` 键的字典。

    工具调用一律走 ``self.call_tool(...)``，它会先问 Harness：
    - 普通工具 → 直接调 MCPServer；
    - 敏感工具 → 走 ``harness.request_approval`` 进入审批队列，
      并返回 ``{"ok": False, "pending_approval": ...}``，由人工批准后再执行。
    """

    name: str = "BaseAgent"

    def __init__(self, mcp, store, harness, llm: LLMClient | None = None) -> None:
        """依赖注入：工具层 / 上下文层 / 安全层 / 模型层。

        参数 llm 为 None 时使用 MockLLM（离线默认）。
        类型注解刻意不写死具体类（mcp: MCPServer 等），方便课程中
        用 duck-typing 替换任意实现。
        """
        self.mcp = mcp
        self.store = store
        self.harness = harness
        self.llm: LLMClient = llm if llm is not None else MockLLM()

    def run(self, context: dict) -> dict:
        """执行 Agent 主逻辑；子类必须实现。返回 {"output": str, ...}。"""
        raise NotImplementedError("子类必须实现 run(context) 方法")

    def call_tool(self, name: str, args: dict) -> dict:
        """统一工具调用入口：敏感工具先过 Harness 审批。

        返回约定：
        - 正常工具：``{"ok": True, "result": ..., "error": None}``（透传 MCP 结果）
        - 敏感工具未批准：``{"ok": False, "pending_approval": 审批记录}``
        """
        if self.harness.is_sensitive(name):
            记录 = self.harness.request_approval(self.name, name, args)
            return {"ok": False, "pending_approval": 记录}
        return self.mcp.call_tool(name, args)
