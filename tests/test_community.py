# -*- coding: utf-8 -*-
"""社区证据内核：行为分级、授权门禁、争议排除与画像接入测试。"""
import unittest
from datetime import datetime, timezone

from intelligence.community.evidence_mapper import (
    behavior_strength, evidence_to_event, is_behavior_usable_for_mastery,
)
from intelligence.student.mastery_estimator import estimate_mastery
from schemas.community import (
    CommunityEvidence, CommunityPost, DiscussionThread,
)
from schemas.learning_event import EventValidationError, LearningEvent
from services.learning_event_service import LearningEventService
from storage.database import Database

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
KP = "multiple_integrals"


def make_evidence(**overrides):
    data = dict(
        evidence_id="ev1",
        student_id="s1",
        post_id="post_001",
        evidence_type="knowledge_application",
        knowledge_points=(KP,),
        evaluation={"correctness": 0.9, "reasoning_quality": 0.8},
        confidence=0.7,
        human_review_status="not_required",
        consent=True,
    )
    data.update(overrides)
    return CommunityEvidence(**data)


class TestBehaviorGating(unittest.TestCase):
    def test_browsing_like_collect_never_enter_mastery(self):
        for behavior in ("view", "like", "collect"):
            self.assertIsNone(behavior_strength(behavior))
            self.assertFalse(is_behavior_usable_for_mastery(behavior))

    def test_substantive_behaviors_have_strength(self):
        self.assertEqual(behavior_strength("post_question"), 0.4)
        self.assertEqual(behavior_strength("delayed_re_explain"), 0.95)

    def test_questioning_a_lot_is_not_low_ability(self):
        # 提问只是中等强度证据，且不因数量多而拉低画像：
        # 提问行为根本不产生计分事件（由上游决定是否生成证据）
        self.assertLess(behavior_strength("post_question"),
                        behavior_strength("answer"))


class TestConsentAndReviewGating(unittest.TestCase):
    def test_unconsented_evidence_dropped(self):
        event = evidence_to_event(
            make_evidence(consent=False), "evt1", "calculus_001", NOW
        )
        self.assertIsNone(event)

    def test_pending_review_dropped(self):
        event = evidence_to_event(
            make_evidence(human_review_status="pending"),
            "evt1", "calculus_001", NOW,
        )
        self.assertIsNone(event)

    def test_disputed_evidence_dropped(self):
        # 学生对 AI 判断提出异议 → 复核前不得写入画像
        event = evidence_to_event(
            make_evidence(human_review_status="disputed"),
            "evt1", "calculus_001", NOW,
        )
        self.assertIsNone(event)

    def test_approved_evidence_passes(self):
        event = evidence_to_event(
            make_evidence(human_review_status="approved"),
            "evt1", "calculus_001", NOW,
        )
        self.assertIsNotNone(event)

    def test_missing_correctness_rejected(self):
        with self.assertRaises(EventValidationError):
            evidence_to_event(
                make_evidence(evaluation={}), "evt1", "calculus_001", NOW
            )


class TestEvidenceConversion(unittest.TestCase):
    def test_event_fields(self):
        event = evidence_to_event(
            make_evidence(), "evt1", "calculus_001", NOW
        )
        self.assertEqual(event.event_type, "community_contribution")
        self.assertEqual(event.source_type, "community")
        self.assertAlmostEqual(event.performance, 0.9, places=6)
        self.assertEqual(event.payload["measurement_kind"], "immediate")

    def test_transfer_evidence_maps_to_transfer_layer(self):
        event = evidence_to_event(
            make_evidence(evidence_type="knowledge_transfer"),
            "evt1", "calculus_001", NOW,
        )
        self.assertEqual(event.payload["measurement_kind"], "transfer")

    def test_community_weight_below_formal_quiz(self):
        # 社区高分（0.9，R=0.6）+ 正式测验低分（0.5，R=1.0）：
        # 加权后 mastery = (0.6·0.9 + 1.0·0.5)/1.6 = 0.65，
        # 即画像被正式测验主导，而非被社区高分拉动。
        community = evidence_to_event(
            make_evidence(), "evt_c", "calculus_001", NOW
        )
        weak_quiz = LearningEvent(
            event_id="evt_q2", student_id="s1", course_id="calculus_001",
            event_type="quiz_submitted", timestamp=NOW,
            knowledge_points=(KP,),
            payload={"score": 10, "total_score": 20},
            source_id="q2", source_type="quiz",
        ).validate()
        est = estimate_mastery([community, weak_quiz], KP, NOW)
        self.assertAlmostEqual(est.mastery, 0.65, places=6)

    def test_community_event_flows_through_event_log(self):
        db = Database(":memory:")
        try:
            service = LearningEventService(db)
            event = evidence_to_event(
                make_evidence(), "evt1", "calculus_001", NOW
            )
            service.record(
                **{k: getattr(event, k) for k in (
                    "event_id", "student_id", "course_id", "event_type",
                    "timestamp", "knowledge_points", "payload",
                    "source_id", "source_type", "verified",
                )}
            )
            effective = service.effective_events("s1", "calculus_001")
            self.assertEqual(len(effective), 1)
            self.assertEqual(effective[0].event_type, "community_contribution")
        finally:
            db.close()


class TestCommunitySchemas(unittest.TestCase):
    def test_post_requires_valid_type(self):
        with self.assertRaises(EventValidationError):
            CommunityPost(
                post_id="p1", author_id="s1", course_id="c1",
                post_type="advertisement", content="...",
            ).validate()

    def test_thread_requires_knowledge_point(self):
        with self.assertRaises(EventValidationError):
            DiscussionThread(
                thread_id="t1", student_id="s1", course_id="c1",
                knowledge_points=(), root_post_id="p1",
            ).validate()

    def test_thread_lifecycle(self):
        thread = DiscussionThread(
            thread_id="t1", student_id="s1", course_id="c1",
            knowledge_points=(KP,), root_post_id="p1",
            reply_ids=("r1", "r2"), status="verified",
            verification_event_id="evt_delayed_1",
        ).validate()
        self.assertEqual(thread.status, "verified")


if __name__ == "__main__":
    unittest.main()
