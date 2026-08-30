"""规划 Agent：查日历/天气 → 生成一周学习计划表。

工作流程（对应课程术语 Tool Use / MCP）：
1. 计算未来一周（周一~周日）的日期；
2. 逐日调用 ``check_calendar`` 与 ``check_weather`` 两个 MCP 工具；
3. 结合 user_profile（运动安排、周三晚上效率低等）与上游诊断结果，
   让 LLM 给出排课思路；
4. 最终输出一张 Markdown 一周计划表（规则拼装，保证离线可读），
   返回 {"output": 表格文本, "tool_calls": [调用过的工具名]}。
"""
from __future__ import annotations

from datetime import date, timedelta

from .base_agent import BaseAgent

# 周一~周日的中文标签，用于表格展示
星期标签 = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class PlannerAgent(BaseAgent):
    """规划 Agent：结合日历、天气与学生习惯生成一周学习计划。"""

    name = "规划Agent"

    def run(self, context: dict) -> dict:
        """执行规划。

        context 由 Orchestrator.select_context 提供（CRISP）：
        user_profile + 诊断 Agent 的 output，不含其他无关历史。
        """
        user_profile: dict = context.get("user_profile", {})
        诊断报告: str = context.get("诊断Agent_output", "")
        城市 = user_profile.get("城市", user_profile.get("city", "北京"))
        运动们: list = user_profile.get("运动", user_profile.get("sports", [])) or []
        薄弱学科 = user_profile.get("薄弱学科", user_profile.get("weak_subject", "目标学科"))

        一周 = self._下周日期()  # list[(date, 星期标签)]

        # 第一步：逐日调用工具，并把 dict 结果**格式化成可读中文**再使用
        每日信息: list[dict] = []
        调用记录: list[str] = []
        for 日, 标签 in 一周:
            日历 = self.call_tool("check_calendar", {"date": 日.isoformat()})
            天气 = self.call_tool("check_weather", {"city": 城市, "date": 日.isoformat()})
            调用记录 += ["check_calendar", "check_weather"]
            每日信息.append(
                {
                    "date": 日,
                    "week": 标签,
                    "calendar": self._格式化日历(日历),
                    "weather": self._格式化天气(天气),
                }
            )

        # 第二步：请 LLM 给出排课思路（MockLLM 会引用运动/周三等信息）
        system = (
            "你是一位学习规划师，请结合一周日历、天气与学生运动习惯，"
            "给出排课原则，尤其要照顾效率低谷时段。"
        )
        user = (
            f"薄弱学科：{薄弱学科}\n"
            f"运动安排：{'、'.join(运动们) if 运动们 else '无'}\n"
            "已知：周三晚上效率低。\n"
            f"诊断结论：{诊断报告 or '（暂无）'}\n"
            "一周日历与天气：\n"
            + "\n".join(
                f"- {d['week']}({d['date'].isoformat()}) 日历:{d['calendar']} 天气:{d['weather']}"
                for d in 每日信息
            )
        )
        思路 = self.llm.chat(system, user)

        # 第三步：按规则拼装 Markdown 一周计划表（离线保底，保证一定有表格）
        表格 = self._生成计划表(每日信息, 运动们, 薄弱学科)
        输出 = f"{思路}\n\n{表格}"
        return {"output": 输出, "tool_calls": 调用记录}

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _下周日期() -> list[tuple[date, str]]:
        """返回从下周一开始连续 7 天的 (日期, 星期标签) 列表。"""
        今天 = date.today()
        下周一 = 今天 + timedelta(days=(7 - 今天.weekday()) % 7 or 7)
        return [(下周一 + timedelta(days=i), 星期标签[i]) for i in range(7)]

    @staticmethod
    def _格式化天气(工具返回: dict) -> str:
        """把 check_weather 的 dict 结果格式化为 "晴 26~33°C（不宜户外运动）" 样式。"""
        if not 工具返回.get("ok"):
            return f"（天气查询失败：{工具返回.get('error')}）"
        r = 工具返回.get("result")
        if not isinstance(r, dict):
            return str(r)
        文本 = f"{r.get('weather', '未知')} {r.get('temperature', '')}".strip()
        if r.get("suitable_for_outdoor") is False:
            文本 += "（不宜户外运动）"
        return 文本

    @staticmethod
    def _格式化日历(工具返回: dict) -> str:
        """把 check_calendar 的 dict 结果格式化为 "19:00-20:30 数学补习班；…" 或 "无安排"。"""
        if not 工具返回.get("ok"):
            return f"（日历查询失败：{工具返回.get('error')}）"
        r = 工具返回.get("result")
        if not isinstance(r, dict):
            return str(r)
        事件们 = r.get("events") or []
        片段们 = [
            f"{e.get('time', '')} {e.get('event', '')}".strip()
            for e in 事件们
            if isinstance(e, dict)
        ]
        return "；".join(p for p in 片段们 if p) or "无安排"

    def _生成计划表(self, 每日信息: list[dict], 运动们: list[str], 薄弱学科: str) -> str:
        """按规则生成一周计划 Markdown 表格。

        规则（对应 MockLLM 思路中的四条原则）：
        - 运动日 → 轻量复习；周三晚上效率低 → 只排短回顾；
        - 其余日子 → 主攻薄弱学科新内容 + 练习。
        """
        行们 = ["| 星期 | 日期 | 天气 | 日历/运动 | 学习任务 |",
                "| --- | --- | --- | --- | --- |"]
        for i, d in enumerate(每日信息):
            标签 = d["week"]
            运动今日 = [s for s in 运动们 if self._运动日命中(s, i)]
            安排 = d["calendar"]
            if 运动今日:
                安排 = f"{安排}；运动：{'、'.join(运动今日)}" if 安排 else f"运动：{'、'.join(运动今日)}"

            if 标签 == "周三":
                任务 = f"晚上效率低：仅 30 分钟回顾{薄弱学科}错题（不排新内容）"
            elif 运动今日:
                任务 = f"轻量复习{薄弱学科}笔记 + 10 道基础题（运动后不加压）"
            elif 标签 in ("周六", "周日"):
                任务 = f"集中攻克{薄弱学科}薄弱点 2 个 + 综合卷 1 套"
            else:
                任务 = f"学习{薄弱学科}新概念 1 个 + 同类练习 20 分钟"
            行们.append(f"| {标签} | {d['date'].isoformat()} | {d['weather']} | {安排 or '无安排'} | {任务} |")
        return "【一周学习计划表】\n" + "\n".join(行们)

    @staticmethod
    def _运动日命中(运动: str, 星期序号: int) -> bool:
        """朴素规则：运动字符串里若含星期字样则按它，否则网球默认周二/周四、游泳默认周六。"""
        映射 = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        命中日 = {
            序号
            for 字, 序号 in 映射.items()
            if f"周{字}" in 运动 or f"星期{字}" in 运动
        }
        if 命中日:
            return 星期序号 in 命中日
        默认 = {"网球": {1, 3}, "游泳": {5}}
        for 项目, 日子 in 默认.items():
            if 项目 in 运动:
                return 星期序号 in 日子
        return False
