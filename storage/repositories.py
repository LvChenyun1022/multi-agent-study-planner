# -*- coding: utf-8 -*-
"""Repository：各实体的存取入口。

learning_events 只提供 append / get / list，不提供 update / delete——
追加纪律同时由数据库触发器兜底（见 database.py）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from schemas.learning_event import LearningEvent
from schemas.student import StudentProfile
from .database import Database


class LearningEventRepository:
    def __init__(self, db: Database):
        self._db = db

    def append(self, event: LearningEvent) -> int:
        """追加事件并返回分配的自增 seq。"""
        event.validate()
        cur = self._db.conn.execute(
            """
            INSERT INTO learning_events (
                event_id, student_id, course_id, event_type, timestamp,
                knowledge_points, payload, source_id, source_type,
                verified, corrects
            ) VALUES (
                :event_id, :student_id, :course_id, :event_type, :timestamp,
                :knowledge_points, :payload, :source_id, :source_type,
                :verified, :corrects
            )
            """,
            event.to_row(),
        )
        self._db.conn.commit()
        event.seq = int(cur.lastrowid)
        return event.seq

    def get(self, event_id: str) -> LearningEvent | None:
        row = self._db.conn.execute(
            "SELECT * FROM learning_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return LearningEvent.from_row(dict(row)) if row else None

    def list(self, student_id: str, course_id: str | None = None) -> list:
        if course_id is None:
            rows = self._db.conn.execute(
                "SELECT * FROM learning_events WHERE student_id = ? ORDER BY seq",
                (student_id,),
            ).fetchall()
        else:
            rows = self._db.conn.execute(
                "SELECT * FROM learning_events "
                "WHERE student_id = ? AND course_id = ? ORDER BY seq",
                (student_id, course_id),
            ).fetchall()
        return [LearningEvent.from_row(dict(row)) for row in rows]

    def latest_seq(self, student_id: str) -> int:
        """当前事件流的最大 seq，作为 evidence_version 使用。"""
        row = self._db.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM learning_events "
            "WHERE student_id = ?",
            (student_id,),
        ).fetchone()
        return int(row["max_seq"])


class StudentRepository:
    def __init__(self, db: Database):
        self._db = db

    def save(self, profile: StudentProfile) -> None:
        profile.validate()
        self._db.conn.execute(
            """
            INSERT INTO students (
                student_id, display_name, academic_year, timezone,
                learning_preferences, available_time, profile_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET
                display_name = excluded.display_name,
                academic_year = excluded.academic_year,
                timezone = excluded.timezone,
                learning_preferences = excluded.learning_preferences,
                available_time = excluded.available_time,
                profile_version = excluded.profile_version,
                updated_at = excluded.updated_at
            """,
            (
                profile.student_id, profile.display_name, profile.academic_year,
                profile.timezone,
                json.dumps(profile.learning_preferences, ensure_ascii=False),
                json.dumps(profile.available_time, ensure_ascii=False),
                profile.profile_version,
                profile.updated_at.isoformat() if profile.updated_at else None,
            ),
        )
        self._db.conn.commit()

    def get(self, student_id: str) -> StudentProfile | None:
        row = self._db.conn.execute(
            "SELECT * FROM students WHERE student_id = ?", (student_id,)
        ).fetchone()
        if not row:
            return None
        profile = StudentProfile(
            student_id=row["student_id"],
            display_name=row["display_name"],
            academic_year=row["academic_year"],
            timezone=row["timezone"],
            learning_preferences=json.loads(row["learning_preferences"]),
            available_time=json.loads(row["available_time"]),
            profile_version=row["profile_version"],
            updated_at=(
                datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None
            ),
        )
        profile.enrolled_courses = tuple(
            r["course_id"]
            for r in self._db.conn.execute(
                "SELECT course_id FROM enrollments WHERE student_id = ? ORDER BY course_id",
                (student_id,),
            ).fetchall()
        )
        return profile

    def enroll(self, student_id: str, course_id: str) -> None:
        self._db.conn.execute(
            "INSERT OR IGNORE INTO enrollments (student_id, course_id) VALUES (?, ?)",
            (student_id, course_id),
        )
        self._db.conn.commit()


class CourseRepository:
    def __init__(self, db: Database):
        self._db = db

    def save(self, spec_dict: dict) -> None:
        self._db.conn.execute(
            "INSERT INTO courses (course_id, spec_json) VALUES (?, ?) "
            "ON CONFLICT(course_id) DO UPDATE SET spec_json = excluded.spec_json",
            (spec_dict["course_id"], json.dumps(spec_dict, ensure_ascii=False)),
        )
        self._db.conn.commit()

    def get(self, course_id: str) -> dict | None:
        row = self._db.conn.execute(
            "SELECT spec_json FROM courses WHERE course_id = ?", (course_id,)
        ).fetchone()
        return json.loads(row["spec_json"]) if row else None


class MasterySnapshotRepository:
    """掌握度快照：保存最近一次画像重建的结果及版本号。"""

    def __init__(self, db: Database):
        self._db = db

    def save_all(self, snapshots: list) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for snap in snapshots:
            self._db.conn.execute(
                """
                INSERT INTO mastery_snapshots (
                    student_id, course_id, knowledge_id, mastery, effective_n,
                    confidence, n_evidence, last_updated, profile_version,
                    evidence_version, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(student_id, course_id, knowledge_id) DO UPDATE SET
                    mastery = excluded.mastery,
                    effective_n = excluded.effective_n,
                    confidence = excluded.confidence,
                    n_evidence = excluded.n_evidence,
                    last_updated = excluded.last_updated,
                    profile_version = excluded.profile_version,
                    evidence_version = excluded.evidence_version,
                    computed_at = excluded.computed_at
                """,
                (
                    snap["student_id"], snap["course_id"], snap["knowledge_id"],
                    snap["mastery"], snap["effective_n"], snap["confidence"],
                    snap["n_evidence"], snap["last_updated"],
                    snap["profile_version"], snap["evidence_version"], now,
                ),
            )
        self._db.conn.commit()

    def list(self, student_id: str, course_id: str | None = None) -> list:
        if course_id is None:
            rows = self._db.conn.execute(
                "SELECT * FROM mastery_snapshots WHERE student_id = ? "
                "ORDER BY course_id, knowledge_id",
                (student_id,),
            ).fetchall()
        else:
            rows = self._db.conn.execute(
                "SELECT * FROM mastery_snapshots "
                "WHERE student_id = ? AND course_id = ? ORDER BY knowledge_id",
                (student_id, course_id),
            ).fetchall()
        return [dict(row) for row in rows]
