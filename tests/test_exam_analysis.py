# -*- coding: utf-8 -*-
"""考试分析 Agent 测试：结构化直通、LLM 抽取、日期换算与端到端桥接。

全部离线运行：LLM 抽取路径使用 FakeLLM 注入固定 JSON，
不依赖真实模型与网络。
"""
from __future__ import annotations

import unittest

from agents.exam_analysis_agent import (
    ExamAnalysisAgent, ExamAnalysisError, _exam_day, _extract_json,
)
from intelligence.decision.exam_plan_bridge import build_exam_plan
from agents.scheduler_adapter import run_exam_plan


def _make_agent(llm=None) -> ExamAnalysisAgent:
    return ExamAnalysisAgent(mcp=None, store=None, harness=None, llm=llm)


class FakeLLM:
    """返回固定文本的假 LLM，满足 chat(system, user) 协议。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


MATH_LLM_JSON = """{
  "knowledge": [
    {"name": "偏导数", "weight": 3},
    {"name": "多元微分", "weight": 2}
  ],
  "tasks": [
    {"name": "偏导数专题", "kp": "偏导数", "hours": 2.5, "required": true, "deps": []},
    {"name": "多元微分综合题", "kp": "多元微分", "hours": 2.5, "required": true,
     "deps": ["偏导数专题"]},
    {"name": "错题本二刷", "kp": "偏导数", "hours": 2.0, "required": false, "deps": []}
  ]
}"""


class TestExtractJson(unittest.TestCase):
    def test_plain_json(self):
        obj = _extract_json(MATH_LLM_JSON)
        self.assertEqual(len(obj["knowledge"]), 2)
        self.assertEqual(len(obj["tasks"]), 3)

    def test_markdown_fence(self):
        obj = _extract_json("```json\n" + MATH_LLM_JSON + "\n```")
        self.assertEqual(obj["knowledge"][0]["name"], "偏导数")

    def test_leading_prose_is_rejected_or_skipped(self):
        obj = _extract_json("先说明一下：\n" + MATH_LLM_JSON)
        self.assertIn("tasks", obj)

    def test_no_json_raises(self):
        with self.assertRaises(ExamAnalysisError):
            _extract_json("这不是 JSON。")


class TestExamDay(unittest.TestCase):
    def test_relative_day_passthrough(self):
        self.assertEqual(_exam_day(None, 3, __import__("datetime").date(2026, 12, 1)), 3)

    def test_date_conversion(self):
        from datetime import date
        self.assertEqual(_exam_day("2026-12-08", None, date(2026, 12, 1)), 7)

    def test_past_exam_rejected(self):
        from datetime import date
        with self.assertRaises(ExamAnalysisError):
            _exam_day("2026-11-30", None, date(2026, 12, 1))

    def test_bad_date_format(self):
        from datetime import date
        with self.assertRaises(ExamAnalysisError):
            _exam_day("12月8日", None, date(2026, 12, 1))

    def test_zero_day_rejected(self):
        from datetime import date
        with self.assertRaises(ExamAnalysisError):
            _exam_day(None, 0, date(2026, 12, 1))


STRUCTURED_COURSE = {
    "name": "线性代数",
    "exam_date": "2026-12-10",
    "credits": 3.0,
    "knowledge": {"矩阵运算": 2, "特征值": 3},
    "tasks": [
        {"name": "矩阵运算强化", "kp": "矩阵运算", "hours": 2.0,
         "required": True, "deps": []},
        {"name": "特征值与特征向量", "kp": "特征值", "hours": 3.0,
         "required": True, "deps": ["矩阵运算强化"]},
    ],
}


class TestStructuredMode(unittest.TestCase):
    def test_structured_input_bypasses_llm(self):
        agent = _make_agent(llm=None)  # MockLLM 也不会被调用
        result = agent.run({
            "course_inputs": [STRUCTURED_COURSE],
            "reference_date": "2026-12-01",
        })
        spec = result["course_specs"][0]
        self.assertEqual(spec.name, "线性代数")
        self.assertEqual(spec.exam_day, 9)
        self.assertEqual(len(spec.tasks), 2)
        self.assertIn("结构化输入", result["output"])

    def test_exam_day_overrides_missing_date(self):
        course = dict(STRUCTURED_COURSE)
        course.pop("exam_date")
        course["exam_day"] = 5
        agent = _make_agent()
        result = agent.run({"course_inputs": [course], "reference_date": "2026-12-01"})
        self.assertEqual(result["course_specs"][0].exam_day, 5)

    def test_missing_both_date_and_day(self):
        course = {k: v for k, v in STRUCTURED_COURSE.items() if k != "exam_date"}
        agent = _make_agent()
        with self.assertRaises(ExamAnalysisError):
            agent.run({"course_inputs": [course], "reference_date": "2026-12-01"})

    def test_empty_inputs_rejected(self):
        agent = _make_agent()
        with self.assertRaises(ExamAnalysisError):
            agent.run({"course_inputs": []})

    def test_dependency_cycle_rejected(self):
        course = dict(STRUCTURED_COURSE)
        course["tasks"] = [
            {"name": "A", "kp": "矩阵运算", "hours": 1.0, "required": True, "deps": ["B"]},
            {"name": "B", "kp": "矩阵运算", "hours": 1.0, "required": True, "deps": ["A"]},
        ]
        agent = _make_agent()
        with self.assertRaises(ExamAnalysisError):
            agent.run({"course_inputs": [course], "reference_date": "2026-12-01"})

    def test_task_with_unknown_kp_rejected(self):
        course = dict(STRUCTURED_COURSE)
        course["tasks"] = [
            {"name": "X", "kp": "不存在的知识点", "hours": 1.0, "required": True, "deps": []},
        ]
        agent = _make_agent()
        with self.assertRaises(ExamAnalysisError):
            agent.run({"course_inputs": [course], "reference_date": "2026-12-01"})


class TestLlmMode(unittest.TestCase):
    def test_llm_extraction(self):
        fake = FakeLLM(MATH_LLM_JSON)
        agent = _make_agent(llm=fake)
        result = agent.run({
            "course_inputs": [{
                "name": "高等数学", "exam_day": 3, "credits": 5.0,
                "scope_text": "第五章偏导数（重点）、第六章多元微分，历年卷一套",
            }],
            "reference_date": "2026-12-01",
        })
        spec = result["course_specs"][0]
        self.assertEqual(spec.name, "高等数学")
        self.assertEqual(spec.exam_day, 3)
        self.assertEqual(spec.knowledge_weights, {"偏导数": 3, "多元微分": 2})
        self.assertEqual(spec.tasks[1].deps, ["偏导数专题"])
        self.assertIn("待确认", result["output"])
        # LLM 只被调用一次，且 system prompt 要求只输出 JSON
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("只输出一个 JSON 对象", fake.calls[0][0])

    def test_llm_reply_with_fence(self):
        fake = FakeLLM("```json\n" + MATH_LLM_JSON + "\n```\n以上。")
        agent = _make_agent(llm=fake)
        result = agent.run({
            "course_inputs": [{"name": "高等数学", "exam_day": 3,
                               "scope_text": "偏导数与多元微分"}],
        })
        self.assertEqual(len(result["course_specs"][0].tasks), 3)

    def test_llm_garbage_raises_clear_error(self):
        fake = FakeLLM("我无法理解这段考试范围。")
        agent = _make_agent(llm=fake)
        with self.assertRaises(ExamAnalysisError) as ctx:
            agent.run({
                "course_inputs": [{"name": "高等数学", "exam_day": 3,
                                   "scope_text": "……"}],
            })
        self.assertIn("MockLLM", str(ctx.exception))

    def test_llm_bad_field_rejected_by_validation(self):
        bad = MATH_LLM_JSON.replace('"hours": 2.5', '"hours": -1', 1)
        fake = FakeLLM(bad)
        agent = _make_agent(llm=fake)
        with self.assertRaises(ExamAnalysisError):
            agent.run({
                "course_inputs": [{"name": "高等数学", "exam_day": 3,
                                   "scope_text": "偏导数"}],
            })

    def test_mock_llm_path_reports_honestly(self):
        agent = _make_agent(llm=None)  # BaseAgent 注入 MockLLM
        with self.assertRaises(ExamAnalysisError):
            agent.run({
                "course_inputs": [{"name": "高等数学", "exam_day": 3,
                                   "scope_text": "偏导数"}],
            })


class TestEndToEnd(unittest.TestCase):
    """Agent 产出 → 桥接层 → 排程引擎，全链路一次性跑通。"""

    def test_agent_output_feeds_scheduler(self):
        fake = FakeLLM(MATH_LLM_JSON)
        agent = _make_agent(llm=fake)
        result = agent.run({
            "course_inputs": [
                {"name": "高等数学", "exam_day": 2, "credits": 5.0,
                 "scope_text": "偏导数、多元微分"},
                STRUCTURED_COURSE,
            ],
            "reference_date": "2026-12-01",
        })
        specs = result["course_specs"]

        student_state = {
            "courses": {
                specs[0].course_id: {"knowledge": {
                    "偏导数": {"mastery": 0.4, "effective_n": 4.0},
                }},
                specs[1].course_id: {"knowledge": {}},
            }
        }
        plan = build_exam_plan(student_state, specs,
                               daily_hours={0: 4.0, 1: 4.0})
        out = run_exam_plan(plan)
        self.assertTrue(out["data"]["conservation_ok"])
        self.assertIn("高等数学", out["report"])
        # 无证据知识点落到中性先验 0.5
        knowledge = plan["courses"][1]["knowledge"]
        self.assertTrue(all(k["mastery"] == 0.5 for k in knowledge))


if __name__ == "__main__":
    unittest.main()
