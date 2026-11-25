# -*- coding: utf-8 -*-
"""PlannerAgent 与 ExamOS 确定性排程的集成测试。"""
import unittest

from agents.planner_agent import PlannerAgent
from agents.scheduler_adapter import SchedulerInputError


def sample_exam_plan(with_feedback=False):
    plan = {
        "daily_hours": {"0": 2.0, "1": 2.0, "2": 2.0},
        "courses": [
            {
                "name": "线性代数", "exam_day": 3, "credits": 4,
                "degree": True, "priority": 0.67, "n_diag": 10,
                "a": 0.8, "b": 0.2, "min_hours": 0.5,
                "knowledge": [{"name": "矩阵运算", "weight": 1.0, "mastery": 0.5}],
                "tasks": [
                    {"name": "矩阵运算强化", "kp": "矩阵运算", "hours": 2.0,
                     "required": True, "deps": []},
                    {"name": "二次型精读", "kp": "二次型", "hours": 1.0,
                     "required": False, "deps": []},
                ],
            },
            {
                "name": "大学英语", "exam_day": 3, "credits": 3,
                "degree": False, "priority": 0.33, "n_diag": 20,
                "a": 0.6, "b": 0.15, "min_hours": 0.5,
                "knowledge": [{"name": "阅读", "weight": 1.0, "mastery": 0.7}],
                "tasks": [
                    {"name": "阅读真题", "kp": "阅读", "hours": 2.0,
                     "required": True, "deps": []},
                ],
            },
        ],
    }
    if with_feedback:
        plan["execution_feedback"] = {
            "day": 0, "completion": {"线性代数": 0.25}, "default_rate": 1.0
        }
    return plan


class FakeMCP:
    def call_tool(self, name, args):
        if name == "check_calendar":
            return {"ok": True, "result": {"date": args["date"], "events": []}}
        if name == "check_weather":
            return {"ok": True, "result": {
                "weather": "晴", "temperature": "20~25°C",
                "suitable_for_outdoor": True,
            }}
        return {"ok": False, "result": None, "error": f"unknown tool: {name}"}


class FakeHarness:
    @staticmethod
    def is_sensitive(name):
        return False


class FakeLLM:
    @staticmethod
    def chat(system, user):
        return "规划思路：以算法分配结果为准，LLM 仅解释安排依据。"


class TestPlannerAgentSchedulerIntegration(unittest.TestCase):
    def setUp(self):
        self.agent = PlannerAgent(FakeMCP(), object(), FakeHarness(), llm=FakeLLM())

    def test_structured_exam_plan_uses_scheduler(self):
        result = self.agent.run({
            "user_profile": {"薄弱学科": "英语", "exam_plan": sample_exam_plan()}
        })
        self.assertEqual(result["planner_mode"], "examos_scheduler")
        self.assertEqual(result["scheduler_result"]["mode"], "v1")
        self.assertIn("ExamOS 确定性排程结果", result["output"])
        self.assertIn("风险评分", result["output"])
        self.assertEqual(len(result["tool_calls"]), 14)

    def test_execution_feedback_triggers_v2_and_conservation(self):
        result = self.agent.run({
            "user_profile": {
                "薄弱学科": "英语",
                "exam_plan": sample_exam_plan(with_feedback=True),
            }
        })
        scheduler_result = result["scheduler_result"]
        self.assertEqual(scheduler_result["mode"], "v2")
        self.assertTrue(scheduler_result["conservation_ok"])
        self.assertIn("v1 / v2 分配差异报告", result["output"])
        self.assertTrue(scheduler_result["allocation_diff"])

    def test_missing_exam_plan_preserves_legacy_weekly_mode(self):
        result = self.agent.run({
            "user_profile": {
                "薄弱学科": "英语",
                "运动": ["网球（周二、周四）"],
            }
        })
        self.assertEqual(result["planner_mode"], "weekly_heuristic")
        self.assertIn("一周学习计划表", result["output"])
        self.assertNotIn("ExamOS 确定性排程结果", result["output"])

    def test_invalid_exam_plan_fails_with_actionable_error(self):
        with self.assertRaises(SchedulerInputError):
            self.agent.run({
                "user_profile": {
                    "exam_plan": {"daily_hours": {"0": 2}, "courses": []}
                }
            })

    def test_replan_from_later_start_day_keeps_day_before_exam(self):
        """回归：start_day=1 时，考前一天（绝对 Day 2 = exam_day-1）必须仍可排课。

        坐标系契约：exam_day 为相对规划原点 day 0 的绝对日序号；
        allocate 用绝对坐标，risk_scores 的 D 用剩余天数视图。
        旧实现把 exam_day 原地平移后再交给 allocate，导致 start_day>0 时
        考试前一天被错误封锁（本例中 Day 2 会完全排空）。
        """
        plan = {
            "start_day": 1,
            "daily_hours": {"1": 2.0, "2": 2.0},
            "courses": [
                {
                    "name": "信号与系统", "exam_day": 3, "credits": 4,
                    "degree": True, "priority": 0.67, "n_diag": 10,
                    "a": 0.8, "b": 0.2, "min_hours": 0.0,
                    "knowledge": [{"name": "傅里叶", "weight": 1.0, "mastery": 0.5}],
                    "tasks": [
                        {"name": "傅里叶变换专题", "kp": "傅里叶", "hours": 4.0,
                         "required": True, "deps": []},
                    ],
                },
            ],
            "execution_feedback": {
                "day": 1, "completion": {"信号与系统": 0.5}, "default_rate": 1.0,
            },
        }
        result = self.agent.run({"user_profile": {"exam_plan": plan}})
        data = result["scheduler_result"]
        self.assertEqual(data["mode"], "v2")
        self.assertTrue(data["conservation_ok"])

        # D 因子按剩余天数计：v1 起点 Day 1 剩 2 天，v2 起点 Day 2 剩 1 天。
        self.assertAlmostEqual(data["risk_scores_initial"]["信号与系统"]["D"], 0.8)
        self.assertAlmostEqual(data["risk_scores_final"]["信号与系统"]["D"], 0.9)

        # 4h 必修：Day 1 计划 2h、完成 50%（done=1h），Day 2 退回 2h；
        # v2 起点 Day 2 预算 2h，应排满考前一天，剩余 1h 构成风险敞口。
        day2_hours = sum(
            row["hours"] for row in data["schedule_final"] if row["day"] == 2
        )
        self.assertAlmostEqual(day2_hours, 2.0)
        state = data["task_state"][0]
        self.assertAlmostEqual(state["done_hours"], 1.0)
        self.assertAlmostEqual(state["remaining_hours"], 1.0)


if __name__ == "__main__":
    unittest.main()
