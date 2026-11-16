# -*- coding: utf-8 -*-
"""学习事件与事件溯源纪律测试。"""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from schemas.learning_event import (
    EventValidationError, LearningEvent,
)
from services.learning_event_service import LearningEventService
from storage.database import Database

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


def make_db():
    return Database(":memory:")


def quiz_event(event_id="e1", **overrides):
    data = dict(
        event_id=event_id,
        student_id="s1",
        course_id="c1",
        event_type="quiz_submitted",
        timestamp=NOW,
        knowledge_points=("multiple_integrals",),
        payload={"score": 13, "total_score": 20},
        source_id="quiz_001",
        source_type="quiz",
    )
    data.update(overrides)
    return LearningEvent(**data)


def record_kwargs(event):
    """从 LearningEvent 提取 LearningEventService.record() 接受的参数。"""
    data = vars(event)
    return {
        key: data[key]
        for key in (
            "event_id", "student_id", "course_id", "event_type", "timestamp",
            "knowledge_points", "payload", "source_id", "source_type",
            "verified",
        )
    }


class TestEventValidation(unittest.TestCase):
    def test_valid_event_passes(self):
        event = quiz_event().validate()
        self.assertEqual(event.performance, 13 / 20)

    def test_unknown_event_type_rejected(self):
        with self.assertRaises(EventValidationError):
            quiz_event(event_type="played_games").validate()

    def test_score_above_total_rejected(self):
        with self.assertRaises(EventValidationError):
            quiz_event(payload={"score": 25, "total_score": 20}).validate()

    def test_scored_event_requires_score(self):
        with self.assertRaises(EventValidationError):
            quiz_event(payload={}).validate()

    def test_bad_timestamp_rejected(self):
        with self.assertRaises(EventValidationError):
            quiz_event(timestamp="昨天").validate()

    def test_z_suffix_timestamp_accepted(self):
        event = quiz_event(timestamp="2026-11-17T10:00:00Z").validate()
        self.assertEqual(event.timestamp.year, 2026)

    def test_non_scored_event_needs_no_score(self):
        event = quiz_event(event_type="study_started", payload={})
        event.validate()
        self.assertIsNone(event.performance)

    def test_empty_student_id_rejected(self):
        with self.assertRaises(EventValidationError):
            quiz_event(student_id="  ").validate()


class TestAppendOnlyLog(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.service = LearningEventService(self.db)

    def tearDown(self):
        self.db.close()

    def test_append_assigns_increasing_seq(self):
        e1 = self.service.record(**record_kwargs(quiz_event("e1")))
        e2 = self.service.record(**record_kwargs(quiz_event("e2")))
        self.assertEqual(e2.seq, e1.seq + 1)

    def test_update_blocked_by_trigger(self):
        self.service.record(**record_kwargs(quiz_event("e1")))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute(
                "UPDATE learning_events SET payload = '{}' WHERE event_id = 'e1'"
            )

    def test_delete_blocked_by_trigger(self):
        self.service.record(**record_kwargs(quiz_event("e1")))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute(
                "DELETE FROM learning_events WHERE event_id = 'e1'"
            )

    def test_duplicate_event_id_rejected(self):
        self.service.record(**record_kwargs(quiz_event("e1")))
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.record(**record_kwargs(quiz_event("e1")))


class TestCorrectionFlow(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.service = LearningEventService(self.db)

    def tearDown(self):
        self.db.close()

    def test_correction_preserves_original_and_supersedes_it(self):
        self.service.record(
            **record_kwargs(quiz_event("e1", payload={"score": 8, "total_score": 20}))
        )
        fixed = self.service.correct(
            "e1", "e1_fix",
            new_payload={"score": 18, "total_score": 20},
            reason="教师复核：第 3 题评分错误",
            timestamp=NOW + timedelta(hours=2),
        )
        self.assertEqual(fixed.corrects, "e1")
        self.assertEqual(fixed.knowledge_points, ("multiple_integrals",))

        # 完整事件流仍可审计：两条都在
        full = self.service.events_for("s1", "c1")
        self.assertEqual([e.event_id for e in full], ["e1", "e1_fix"])

        # 有效视图只剩纠正后的版本
        effective = self.service.effective_events("s1", "c1")
        self.assertEqual([e.event_id for e in effective], ["e1_fix"])
        self.assertEqual(effective[0].performance, 18 / 20)

    def test_correcting_missing_event_rejected(self):
        with self.assertRaises(EventValidationError):
            self.service.correct("ghost", "e2", {}, "无理由")

    def test_unverified_events_excluded_from_effective(self):
        self.service.record(**record_kwargs(quiz_event("e1", verified=False)))
        self.assertEqual(self.service.effective_events("s1", "c1"), [])
        self.assertEqual(len(self.service.events_for("s1", "c1")), 1)

    def test_evidence_version_tracks_seq(self):
        self.assertEqual(self.service.evidence_version("s1"), 0)
        self.service.record(**record_kwargs(quiz_event("e1")))
        self.service.record(**record_kwargs(quiz_event("e2")))
        self.assertEqual(self.service.evidence_version("s1"), 2)


if __name__ == "__main__":
    unittest.main()
