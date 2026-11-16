# -*- coding: utf-8 -*-
"""长期保持：遗忘拟合、五态知识状态机与复习优先级测试。"""
import math
import unittest
from datetime import datetime, timedelta, timezone

from intelligence.student.retention import (
    build_retention_profile, classify_knowledge_state, fit_forgetting_rate,
    forgetting_probability, review_priority,
)
from schemas.learning_event import LearningEvent

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
KP = "multiple_integrals"
DAY0 = NOW - timedelta(days=10)


def ev(event_id, performance, *, event_type, days_after_day0, seq=None):
    return LearningEvent(
        event_id=event_id, student_id="s1", course_id="c1",
        event_type=event_type,
        timestamp=DAY0 + timedelta(days=days_after_day0),
        knowledge_points=(KP,),
        payload={"score": performance * 20, "total_score": 20},
        seq=seq,
    ).validate()


class TestKnowledgeStateMachine(unittest.TestCase):
    def test_unlearned_without_evidence(self):
        state, _ = classify_knowledge_state([], KP)
        self.assertEqual(state, "unlearned")

    def test_immediate_evidence_gives_acquired(self):
        events = [ev("e1", 0.8, event_type="quiz_submitted",
                     days_after_day0=0)]
        state, _ = classify_knowledge_state(events, KP)
        self.assertEqual(state, "acquired")

    def test_independent_evidence_gives_independently_retrievable(self):
        events = [ev("e1", 0.8, event_type="diagnostic_quiz_submitted",
                     days_after_day0=1)]
        state, _ = classify_knowledge_state(events, KP)
        self.assertEqual(state, "independently_retrievable")

    def test_two_delayed_points_give_retained(self):
        events = [
            ev("e1", 0.85, event_type="diagnostic_quiz_submitted",
               days_after_day0=1, seq=1),
            ev("e2", 0.75, event_type="delayed_test_completed",
               days_after_day0=3, seq=2),
            ev("e3", 0.7, event_type="delayed_test_completed",
               days_after_day0=7, seq=3),
        ]
        state, _ = classify_knowledge_state(events, KP)
        self.assertEqual(state, "retained")

    def test_transfer_evidence_gives_transferable(self):
        events = [
            ev("e1", 0.85, event_type="diagnostic_quiz_submitted",
               days_after_day0=1, seq=1),
            ev("e2", 0.75, event_type="delayed_test_completed",
               days_after_day0=3, seq=2),
            ev("e3", 0.7, event_type="delayed_test_completed",
               days_after_day0=7, seq=3),
            ev("e4", 0.8, event_type="transfer_test_completed",
               days_after_day0=8, seq=4),
        ]
        state, _ = classify_knowledge_state(events, KP)
        self.assertEqual(state, "transferable")

    def test_recent_delayed_collapse_demotes_state(self):
        # 曾经 retained，但最新延迟测试只有 30% → 打回 acquired
        events = [
            ev("e1", 0.85, event_type="diagnostic_quiz_submitted",
               days_after_day0=1, seq=1),
            ev("e2", 0.75, event_type="delayed_test_completed",
               days_after_day0=3, seq=2),
            ev("e3", 0.7, event_type="delayed_test_completed",
               days_after_day0=7, seq=3),
            ev("e4", 0.3, event_type="delayed_test_completed",
               days_after_day0=9, seq=4),
        ]
        state, notes = classify_knowledge_state(events, KP)
        self.assertEqual(state, "acquired")
        self.assertTrue(any("降级" in note for note in notes))

    def test_short_term_tutoring_spike_does_not_reach_retained(self):
        # 辅导后即时 90%，无任何延迟证据：最高只能到 acquired
        events = [ev("e1", 0.9, event_type="quiz_submitted",
                     days_after_day0=0)]
        state, _ = classify_knowledge_state(events, KP)
        self.assertEqual(state, "acquired")


class TestForgettingModel(unittest.TestCase):
    def test_lambda_fit_on_declining_delayed_points(self):
        events = [
            ev("e1", 0.8, event_type="delayed_test_completed",
               days_after_day0=3, seq=1),
            ev("e2", 0.6, event_type="delayed_test_completed",
               days_after_day0=7, seq=2),
        ]
        lam = fit_forgetting_rate(events, DAY0)
        self.assertIsNotNone(lam)
        self.assertGreater(lam, 0.0)
        # 解析解：ln(p)=a−λt，两点 → λ=(ln0.8−ln0.6)/(7−3)
        self.assertAlmostEqual(lam, (math.log(0.8) - math.log(0.6)) / 4,
                               places=6)

    def test_lambda_none_with_single_point(self):
        events = [ev("e1", 0.8, event_type="delayed_test_completed",
                     days_after_day0=3)]
        self.assertIsNone(fit_forgetting_rate(events, DAY0))

    def test_forgetting_probability(self):
        p = forgetting_probability(0.1, 7.0)
        self.assertAlmostEqual(p, 1.0 - math.exp(-0.7), places=9)
        self.assertIsNone(forgetting_probability(None, 7.0))

    def test_profile_marks_retention_unknown_without_delayed(self):
        events = [ev("e1", 0.9, event_type="quiz_submitted",
                     days_after_day0=0)]
        profile = build_retention_profile(events, KP, NOW)
        self.assertTrue(profile.retention_unknown)
        self.assertIsNone(profile.forgetting_rate)
        self.assertTrue(any("未知" in note for note in profile.notes))

    def test_profile_full_lifecycle(self):
        events = [
            ev("e1", 0.85, event_type="diagnostic_quiz_submitted",
               days_after_day0=1, seq=1),
            ev("e2", 0.75, event_type="delayed_test_completed",
               days_after_day0=3, seq=2),
            ev("e3", 0.7, event_type="delayed_test_completed",
               days_after_day0=7, seq=3),
        ]
        profile = build_retention_profile(events, KP, NOW)
        self.assertEqual(profile.state, "retained")
        self.assertFalse(profile.retention_unknown)
        self.assertEqual(profile.delayed_points, 2)
        self.assertIsNotNone(profile.forgetting_rate)


class TestReviewPriority(unittest.TestCase):
    def test_priority_formula_with_known_lambda(self):
        lam = 0.1
        expected = 0.9 * (1 - 0.4) * (1 - math.exp(-lam * 7)) * 1.0
        self.assertAlmostEqual(
            review_priority(0.9, 0.4, lam, effective_n=5.0,
                            horizon_days=7.0),
            expected, places=9,
        )

    def test_unknown_lambda_falls_back_to_evidence_scarcity(self):
        # 无延迟证据且样本量为 0 → P_forget 兜底为 1.0
        priority = review_priority(0.8, 0.3, None, effective_n=0.0)
        self.assertAlmostEqual(priority, 0.8 * 0.7 * 1.0, places=9)
        # 样本量增大 → 未知风险降低
        lower = review_priority(0.8, 0.3, None, effective_n=4.0)
        self.assertLess(lower, priority)

    def test_mastered_important_knowledge_gets_low_priority(self):
        low = review_priority(0.9, 0.95, 0.05, effective_n=5.0)
        high = review_priority(0.9, 0.2, 0.05, effective_n=5.0)
        self.assertLess(low, high)


if __name__ == "__main__":
    unittest.main()
