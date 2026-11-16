# -*- coding: utf-8 -*-
"""证据分级、独立性 V 因子与能力验证触发器测试。"""
import unittest
from datetime import datetime, timedelta, timezone

from intelligence.assessment.competency_triggers import recommend_assessment
from intelligence.assessment.evidence_policy import (
    ASSISTANCE_MULTIPLIER, independence_of, is_independent_evidence,
    measurement_kind, reliability_of,
)
from intelligence.student.mastery_estimator import estimate_mastery
from schemas.learning_event import LearningEvent

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
KP = "multiple_integrals"


def ev(event_id, performance, *, event_type="quiz_submitted",
       assistance=None, days_ago=0.0, source_id=None, seq=None):
    payload = {"score": performance * 20, "total_score": 20}
    if assistance is not None:
        payload["assistance"] = assistance
    return LearningEvent(
        event_id=event_id, student_id="s1", course_id="c1",
        event_type=event_type,
        timestamp=NOW - timedelta(days=days_ago),
        knowledge_points=(KP,), payload=payload,
        source_id=source_id, source_type=None, seq=seq,
    ).validate()


class TestEvidencePolicy(unittest.TestCase):
    def test_assistance_levels_map_to_multipliers(self):
        heavy = ev("e1", 0.9, event_type="assignment_submitted",
                   assistance="declared_heavy")
        clean = ev("e2", 0.9, event_type="assignment_submitted",
                   assistance="none")
        self.assertEqual(independence_of(heavy), 0.15)
        self.assertEqual(independence_of(clean), 1.0)
        self.assertEqual(independence_of(ev("e3", 0.9)), 0.9)  # 未申报

    def test_invalid_assistance_rejected(self):
        with self.assertRaises(Exception):
            ev("e1", 0.9, assistance="maybe")

    def test_ai_assisted_high_score_does_not_dominate_mastery(self):
        # AI 重度辅助的作业 95%（V=0.15）+ 独立完成的作业 55%（V=1.0）
        events = [
            ev("e1", 0.95, event_type="assignment_submitted",
               assistance="declared_heavy", source_id="a1", seq=1),
            ev("e2", 0.55, event_type="assignment_submitted",
               assistance="none", source_id="a2", seq=2),
        ]
        estimate = estimate_mastery(events, KP, NOW)
        # 权重比 0.15:1.0 → mastery ≈ 0.60，而不是表面平均 0.75
        self.assertAlmostEqual(
            estimate.mastery,
            (0.15 * 0.95 + 1.0 * 0.55) / 1.15, places=6,
        )
        self.assertLess(estimate.mastery, 0.62)

    def test_measurement_kind_layers(self):
        self.assertEqual(measurement_kind(ev("e1", 0.8)), "immediate")
        self.assertEqual(measurement_kind(
            ev("e2", 0.8, event_type="diagnostic_quiz_submitted")),
            "independent")
        self.assertEqual(measurement_kind(
            ev("e3", 0.8, event_type="delayed_test_completed")), "delayed")
        self.assertEqual(measurement_kind(
            ev("e4", 0.8, event_type="transfer_test_completed")), "transfer")

    def test_independent_evidence_classification(self):
        self.assertTrue(is_independent_evidence(
            ev("e1", 0.8, event_type="diagnostic_quiz_submitted")))
        self.assertTrue(is_independent_evidence(
            ev("e2", 0.8, event_type="assignment_submitted",
               assistance="none")))
        self.assertFalse(is_independent_evidence(
            ev("e3", 0.8, event_type="assignment_submitted",
               assistance="declared_heavy")))

    def test_reliability_defaults_by_event_type(self):
        event = ev("e1", 0.8, source_id="q1")
        self.assertEqual(reliability_of(event), 1.0)
        exam = ev("e2", 0.8, event_type="exam_completed")
        self.assertEqual(reliability_of(exam), 1.2)


class TestCompetencyTriggers(unittest.TestCase):
    def test_no_evidence_triggers_insufficiency(self):
        rec = recommend_assessment([], KP, NOW, effective_n=0.0)
        self.assertTrue(rec.triggered)
        self.assertIn("独立证据不足", rec.reasons[0])

    def test_homework_independent_divergence_triggers(self):
        # 作业全对（AI 重度辅助申报）+ 独立速测 55%：背离 0.40 > τ_D
        events = [
            ev("h1", 0.95, event_type="assignment_submitted",
               assistance="declared_heavy", source_id="h1", seq=1),
            ev("h2", 0.92, event_type="assignment_submitted",
               assistance="declared_heavy", source_id="h2", seq=2),
            ev("d1", 0.55, event_type="diagnostic_quiz_submitted",
               source_id="d1", seq=3),
        ]
        rec = recommend_assessment(events, KP, NOW, effective_n=2.0)
        self.assertTrue(rec.triggered)
        self.assertTrue(any("背离" in r for r in rec.reasons))
        self.assertAlmostEqual(rec.homework_performance, 0.935, places=3)
        self.assertAlmostEqual(rec.independent_performance, 0.55, places=3)

    def test_stale_independent_evidence_triggers(self):
        events = [
            ev("d1", 0.8, event_type="diagnostic_quiz_submitted",
               days_ago=20, source_id="d1", seq=1),
            ev("d2", 0.75, event_type="diagnostic_quiz_submitted",
               days_ago=20, source_id="d2", seq=2),
        ]
        rec = recommend_assessment(events, KP, NOW, effective_n=2.0)
        self.assertTrue(rec.triggered)
        self.assertEqual(len(rec.reasons), 1)
        self.assertIn("20 天", rec.reasons[0])

    def test_recent_sufficient_evidence_does_not_trigger(self):
        events = [
            ev("d1", 0.8, event_type="diagnostic_quiz_submitted",
               days_ago=2, source_id="d1", seq=1),
            ev("d2", 0.75, event_type="diagnostic_quiz_submitted",
               days_ago=1, source_id="d2", seq=2),
        ]
        rec = recommend_assessment(events, KP, NOW, effective_n=2.0)
        self.assertFalse(rec.triggered)
        self.assertEqual(rec.reasons, [])


if __name__ == "__main__":
    unittest.main()
