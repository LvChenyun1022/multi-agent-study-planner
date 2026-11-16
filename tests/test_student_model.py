# -*- coding: utf-8 -*-
"""Student Modeling Engine：证据加权掌握度估计测试。"""
import unittest
from datetime import datetime, timedelta, timezone

from intelligence.student.mastery_estimator import (
    confidence_level, dedupe_by_source, estimate_mastery,
)
from intelligence.student.profile_builder import (
    PRIOR_MASTERY, build_student_state,
)
from schemas.course import CourseSpec, TaskSpec
from schemas.learning_event import LearningEvent

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
KP = "multiple_integrals"


def evidence_event(event_id, performance, *, days_ago=0.0, source_id=None,
                   source_type="quiz", event_type="quiz_submitted",
                   difficulty=None, kp=KP, seq=None):
    payload = {"score": performance * 20, "total_score": 20}
    if difficulty is not None:
        payload["difficulty"] = difficulty
    return LearningEvent(
        event_id=event_id,
        student_id="s1",
        course_id="c1",
        event_type=event_type,
        timestamp=NOW - timedelta(days=days_ago),
        knowledge_points=(kp,),
        payload=payload,
        source_id=source_id,
        source_type=source_type,
        seq=seq,
    ).validate()


class TestMasteryEstimator(unittest.TestCase):
    def test_no_evidence_returns_none_mastery(self):
        estimate = estimate_mastery([], KP, NOW)
        self.assertIsNone(estimate.mastery)
        self.assertEqual(estimate.confidence, "none")
        self.assertEqual(estimate.effective_n, 0.0)
        self.assertEqual(estimate.mastery_or_prior, 0.5)

    def test_single_evidence_mastery_equals_performance(self):
        estimate = estimate_mastery([evidence_event("e1", 0.65)], KP, NOW)
        self.assertAlmostEqual(estimate.mastery, 0.65, places=6)

    def test_higher_reliability_evidence_dominates(self):
        # 今天的正式考试（权重 1.2，全对）+ 今天的学生自评（权重 0.3，全错）
        events = [
            evidence_event("e1", 1.0, source_type="exam",
                           event_type="exam_completed"),
            evidence_event("e2", 0.0, source_type="self_assessment",
                           event_type="self_assessment"),
        ]
        estimate = estimate_mastery(events, KP, NOW)
        self.assertAlmostEqual(estimate.mastery, 1.2 / 1.5, places=6)

    def test_recency_decay_halves_weight_after_half_life(self):
        # 今天全对（w=1），14 天前全错（w=0.5）
        events = [
            evidence_event("e1", 1.0, days_ago=0),
            evidence_event("e2", 0.0, days_ago=14),
        ]
        estimate = estimate_mastery(events, KP, NOW)
        self.assertAlmostEqual(estimate.mastery, 1.0 / 1.5, places=6)

    def test_harder_questions_carry_more_weight(self):
        # 简单题（difficulty=0 → w=0.5）全对；难题（difficulty=1 → w=1.5）全错
        events = [
            evidence_event("e1", 1.0, difficulty=0.0),
            evidence_event("e2", 0.0, difficulty=1.0),
        ]
        estimate = estimate_mastery(events, KP, NOW)
        self.assertAlmostEqual(estimate.mastery, 0.5 / 2.0, places=6)

    def test_same_source_practice_counts_once(self):
        # 同一道题刷三次，只保留最新一次（40%），不产生三份证据
        events = [
            evidence_event("e1", 0.9, source_id="q_001", days_ago=3, seq=1),
            evidence_event("e2", 0.8, source_id="q_001", days_ago=2, seq=2),
            evidence_event("e3", 0.4, source_id="q_001", days_ago=1, seq=3),
        ]
        estimate = estimate_mastery(events, KP, NOW)
        self.assertEqual(estimate.n_evidence, 1)
        self.assertAlmostEqual(estimate.mastery, 0.4, places=6)

    def test_dedupe_keeps_distinct_sources(self):
        events = [
            evidence_event("e1", 0.9, source_id="q_001", seq=1),
            evidence_event("e2", 0.7, source_id="q_002", seq=2),
        ]
        self.assertEqual(len(dedupe_by_source(events)), 2)

    def test_effective_sample_size_of_equal_weights(self):
        events = [
            evidence_event("e1", 0.8, source_id="q1"),
            evidence_event("e2", 0.6, source_id="q2"),
        ]
        estimate = estimate_mastery(events, KP, NOW)
        self.assertAlmostEqual(estimate.effective_n, 2.0, places=6)
        self.assertEqual(estimate.confidence, "medium")

    def test_unrelated_knowledge_point_ignored(self):
        events = [evidence_event("e1", 0.9, kp="limits")]
        estimate = estimate_mastery(events, KP, NOW)
        self.assertIsNone(estimate.mastery)

    def test_confidence_thresholds(self):
        self.assertEqual(confidence_level(0.0), "none")
        self.assertEqual(confidence_level(1.0), "low")
        self.assertEqual(confidence_level(2.0), "medium")
        self.assertEqual(confidence_level(3.5), "high")


def make_course(course_id="c1", name="高等数学", exam_day=5):
    return CourseSpec(
        course_id=course_id,
        name=name,
        exam_day=exam_day,
        knowledge_weights={KP: 0.6, "limits": 0.4},
        tasks=[
            TaskSpec(name="重积分专题", kp=KP, hours=3.0, required=True),
            TaskSpec(name="极限回顾", kp="limits", hours=1.0, required=False),
        ],
    ).validate()


class TestProfileBuilder(unittest.TestCase):
    def test_prior_for_knowledge_without_evidence(self):
        state = build_student_state(
            student_id="s1",
            course_specs=[make_course()],
            events_by_course={"c1": []},
            profile_version=1,
            evidence_version=0,
            now=NOW,
        )
        entry = state["courses"]["c1"]["knowledge"][KP]
        self.assertEqual(entry["mastery"], PRIOR_MASTERY)
        self.assertTrue(entry["is_prior"])
        self.assertEqual(entry["confidence"], "none")

    def test_measured_and_prior_knowledge_coexist(self):
        events = [evidence_event("e1", 0.45)]
        state = build_student_state(
            student_id="s1",
            course_specs=[make_course()],
            events_by_course={"c1": events},
            profile_version=2,
            evidence_version=1,
            now=NOW,
        )
        knowledge = state["courses"]["c1"]["knowledge"]
        self.assertAlmostEqual(knowledge[KP]["mastery"], 0.45, places=6)
        self.assertFalse(knowledge[KP]["is_prior"])
        self.assertTrue(knowledge["limits"]["is_prior"])
        self.assertEqual(state["profile_version"], 2)
        self.assertEqual(state["evidence_version"], 1)


if __name__ == "__main__":
    unittest.main()
