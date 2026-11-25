# -*- coding: utf-8 -*-
"""进度监控 Agent 测试：四类触发信号、显著性阈值、冷却闸门与反馈输出。

全部离线运行；触发判定为纯确定性逻辑，不涉及 LLM。
"""
from __future__ import annotations

import unittest

from agents.progress_monitor_agent import (
    ProgressMonitorAgent, ProgressMonitorError,
)


def _run(context: dict) -> dict:
    agent = ProgressMonitorAgent(mcp=None, store=None, harness=None)
    return agent.run(context)


BASE = {
    "day": 0,
    "now": "2026-12-01T20:00:00",
    "plan": {"schedule": {"0": [
        {"course": "高等数学", "task": "偏导数专题", "hours": 1.0},
        {"course": "线性代数", "task": "矩阵运算强化", "hours": 2.0},
        {"course": "大学英语", "task": "核心词汇过一遍", "hours": 1.0},
    ]}},
}


class TestCompletionTrigger(unittest.TestCase):
    def test_high_completion_no_trigger(self):
        result = _run({**BASE, "completion": {
            "高等数学": 1.0, "线性代数": 0.9, "大学英语": 1.0}})
        self.assertFalse(result["triggered"])
        self.assertIsNone(result["execution_feedback"])
        self.assertIn("继续执行当前计划", result["output"])

    def test_single_day_deviation_triggers(self):
        """线代完成率 25%（偏差 75% > 40%）→ 触发，产出 execution_feedback。"""
        result = _run({**BASE, "completion": {
            "高等数学": 1.0, "线性代数": 0.25, "大学英语": 1.0}})
        self.assertTrue(result["triggered"])
        courses = [t["course"] for t in result["triggers"]]
        self.assertEqual(courses, ["线性代数"])
        feedback = result["execution_feedback"]
        self.assertEqual(feedback["day"], 0)
        self.assertEqual(feedback["completion"]["线性代数"], 0.25)
        self.assertIn("剩余工作量回补 1.5h", result["output"])
        self.assertIn("没复习不等于更不会", result["output"])

    def test_consecutive_days_trigger(self):
        """单日偏差未超阈值，但连续两日低于 60% → 触发。"""
        result = _run({**BASE,
                       "thresholds": {"daily_deviation": 0.5},
                       "history": {"recent_completion": {"线性代数": [0.55]}},
                       "completion": {"线性代数": 0.58}})
        self.assertTrue(result["triggered"])
        self.assertIn("连续两日", result["triggers"][0]["reasons"][0])

    def test_minor_fluctuation_ignored(self):
        """完成率 0.7（偏差 30% < 40%）且无历史 → 不触发。"""
        result = _run({**BASE, "completion": {"线性代数": 0.7}})
        self.assertFalse(result["triggered"])


class TestExternalSignals(unittest.TestCase):
    def test_mastery_change_triggers(self):
        result = _run({**BASE,
                       "signals": {"mastery_changed": ["线性代数"]},
                       "completion": {"线性代数": 0.95}})
        self.assertTrue(result["triggered"])
        self.assertEqual(result["triggers"][0]["kinds"], ["mastery"])

    def test_constraints_change_triggers(self):
        result = _run({**BASE,
                       "signals": {"constraints_changed": True},
                       "completion": {"线性代数": 1.0}})
        self.assertTrue(result["triggered"])

    def test_safety_line_triggers(self):
        result = _run({**BASE,
                       "course_status": {"高等数学": {
                           "days_to_exam": 2, "required_unfinished_ratio": 0.6}},
                       "completion": {"高等数学": 1.0}})
        self.assertTrue(result["triggered"])
        self.assertIn("安全线", result["triggers"][0]["reasons"][0])

    def test_safety_line_not_crossed(self):
        result = _run({**BASE,
                       "course_status": {"高等数学": {
                           "days_to_exam": 2, "required_unfinished_ratio": 0.3}},
                       "completion": {"高等数学": 1.0}})
        self.assertFalse(result["triggered"])


class TestCooldown(unittest.TestCase):
    def test_completion_signal_suppressed_within_cooldown(self):
        result = _run({**BASE,
                       "history": {"last_replan": {
                           "线性代数": "2026-12-01T08:00:00"}},
                       "completion": {"线性代数": 0.25}})
        self.assertFalse(result["triggered"])
        self.assertIsNone(result["execution_feedback"])
        self.assertEqual(result["suppressed"][0]["course"], "线性代数")
        self.assertAlmostEqual(
            result["suppressed"][0]["cooldown_remaining_h"], 12.0)
        self.assertIn("冷却期", result["output"])

    def test_cooldown_expired_triggers(self):
        result = _run({**BASE,
                       "history": {"last_replan": {
                           "线性代数": "2026-11-30T18:00:00"}},
                       "completion": {"线性代数": 0.25}})
        self.assertTrue(result["triggered"])

    def test_mastery_signal_bypasses_cooldown(self):
        """掌握度变化是非同类信号，不受完成率冷却限制。"""
        result = _run({**BASE,
                       "history": {"last_replan": {
                           "线性代数": "2026-12-01T19:00:00"}},
                       "signals": {"mastery_changed": ["线性代数"]},
                       "completion": {"线性代数": 0.95}})
        self.assertTrue(result["triggered"])

    def test_manual_request_bypasses_cooldown(self):
        result = _run({**BASE,
                       "history": {"last_replan": {
                           "线性代数": "2026-12-01T19:30:00"}},
                       "signals": {"manual_request": True},
                       "completion": {"线性代数": 0.9}})
        self.assertTrue(result["triggered"])


class TestInputValidation(unittest.TestCase):
    def test_empty_completion_rejected(self):
        with self.assertRaises(ProgressMonitorError):
            _run({"completion": {}})

    def test_rate_out_of_range(self):
        with self.assertRaises(ProgressMonitorError):
            _run({"completion": {"线性代数": 1.5}})

    def test_bad_time_format(self):
        with self.assertRaises(ProgressMonitorError):
            _run({**BASE, "now": "昨晚八点",
                  "completion": {"线性代数": 0.5}})


class TestSchedulerIntegration(unittest.TestCase):
    """触发输出的 execution_feedback 直接接入排程引擎，产出 v2。"""

    def test_feedback_feeds_run_exam_plan(self):
        from agents.scheduler_adapter import run_exam_plan

        result = _run({**BASE, "completion": {
            "高等数学": 1.0, "线性代数": 0.25, "大学英语": 1.0}})
        feedback = result["execution_feedback"]
        self.assertIsNotNone(feedback)

        exam_plan = {
            "courses": [
                {
                    "name": "高等数学", "exam_day": 1, "credits": 5.0,
                    "degree": True, "priority": 1.0, "n_diag": 8,
                    "a": 2.0, "b": 0.5, "min_hours": 1.0,
                    "knowledge": [{"name": "偏导数", "weight": 1.0, "mastery": 0.4}],
                    "tasks": [{"name": "偏导数专题", "kp": "偏导数",
                               "hours": 1.0, "required": True, "deps": []}],
                },
                {
                    "name": "线性代数", "exam_day": 3, "credits": 3.0,
                    "degree": True, "priority": 0.8, "n_diag": 5,
                    "a": 2.0, "b": 0.5, "min_hours": 1.0,
                    "knowledge": [{"name": "矩阵运算", "weight": 1.0, "mastery": 0.4}],
                    "tasks": [{"name": "矩阵运算强化", "kp": "矩阵运算",
                               "hours": 2.0, "required": True, "deps": []}],
                },
                {
                    "name": "大学英语", "exam_day": 5, "credits": 2.0,
                    "degree": False, "priority": 0.5, "n_diag": 12,
                    "a": 2.0, "b": 0.5, "min_hours": 0.0,
                    "knowledge": [{"name": "词汇", "weight": 1.0, "mastery": 0.8}],
                    "tasks": [{"name": "核心词汇过一遍", "kp": "词汇",
                               "hours": 1.0, "required": True, "deps": []}],
                },
            ],
            "daily_hours": {0: 4.0, 1: 4.0, 2: 4.0},
            "start_day": 0,
            "execution_feedback": feedback,
        }
        out = run_exam_plan(exam_plan)
        self.assertEqual(out["data"]["mode"], "v2")
        self.assertTrue(out["data"]["conservation_ok"])
        courses_in_feedback = {row["course"] for row in out["data"]["feedback"]}
        self.assertIn("线性代数", courses_in_feedback)


if __name__ == "__main__":
    unittest.main()
