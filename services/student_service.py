# -*- coding: utf-8 -*-
"""StudentService：学生档案、选课与画像重建的统一入口。

rebuild_state() 是「学习事件 → 学生画像」流水线的核心：
读取有效事件流 → 掌握度估计 → 递增 profile_version →
写回学生档案与掌握度快照。任何时候都可以用事件流重放
重建任意版本的画像，保证画像变化可解释。
"""
from __future__ import annotations

from datetime import datetime, timezone

from intelligence.student.profile_builder import build_student_state
from schemas.course import CourseSpec, TaskSpec
from schemas.learning_event import EventValidationError
from schemas.student import StudentProfile
from storage.database import Database
from storage.repositories import (
    CourseRepository, MasterySnapshotRepository, StudentRepository,
)
from .learning_event_service import LearningEventService


def course_spec_from_dict(data: dict) -> CourseSpec:
    """把存储层/接口层的课程字典还原为 CourseSpec 并校验。"""
    try:
        spec = CourseSpec(
            course_id=data["course_id"],
            name=data["name"],
            exam_day=data["exam_day"],
            credits=data.get("credits", 3.0),
            degree=data.get("degree", True),
            priority=data.get("priority", 0.5),
            a=data.get("a", 2.0),
            b=data.get("b", 0.5),
            min_hours=data.get("min_hours", 0.0),
            knowledge_weights=dict(data.get("knowledge_weights", {})),
            tasks=[
                TaskSpec(
                    name=t["name"], kp=t["kp"], hours=t["hours"],
                    required=t["required"], deps=list(t.get("deps", [])),
                )
                for t in data.get("tasks", [])
            ],
        )
    except KeyError as exc:
        raise EventValidationError(f"课程档案缺字段：{exc}") from None
    return spec.validate()


def course_spec_to_dict(spec: CourseSpec) -> dict:
    return {
        "course_id": spec.course_id,
        "name": spec.name,
        "exam_day": spec.exam_day,
        "credits": spec.credits,
        "degree": spec.degree,
        "priority": spec.priority,
        "a": spec.a,
        "b": spec.b,
        "min_hours": spec.min_hours,
        "knowledge_weights": dict(spec.knowledge_weights),
        "tasks": [
            {"name": t.name, "kp": t.kp, "hours": t.hours,
             "required": t.required, "deps": list(t.deps)}
            for t in spec.tasks
        ],
    }


class StudentService:
    def __init__(self, db: Database):
        self._db = db
        self._students = StudentRepository(db)
        self._courses = CourseRepository(db)
        self._snapshots = MasterySnapshotRepository(db)
        self._events = LearningEventService(db)

    # ---------------------------------------------------------- 档案与选课
    def get_or_create(self, student_id: str, **kwargs) -> StudentProfile:
        profile = self._students.get(student_id)
        if profile is None:
            profile = StudentProfile(student_id=student_id, **kwargs).validate()
            self._students.save(profile)
        return profile

    def register_course(self, student_id: str, spec: CourseSpec) -> None:
        """登记课程档案并选课。"""
        self.get_or_create(student_id)
        self._courses.save(course_spec_to_dict(spec.validate()))
        self._students.enroll(student_id, spec.course_id)

    def course_specs_for(self, student_id: str) -> list:
        profile = self._students.get(student_id)
        if profile is None:
            raise EventValidationError(f"学生不存在：{student_id}")
        specs = []
        for course_id in profile.enrolled_courses:
            data = self._courses.get(course_id)
            if data is None:
                raise EventValidationError(f"课程档案缺失：{course_id}")
            specs.append(course_spec_from_dict(data))
        return specs

    # ---------------------------------------------------------- 画像重建
    def rebuild_state(self, student_id: str,
                      now: datetime | None = None) -> dict:
        """重放有效事件流，重建学生画像并持久化快照。"""
        now = now or datetime.now(timezone.utc)
        profile = self._students.get(student_id)
        if profile is None:
            raise EventValidationError(f"学生不存在：{student_id}")
        specs = self.course_specs_for(student_id)
        events_by_course = {
            spec.course_id: self._events.effective_events(student_id,
                                                          spec.course_id)
            for spec in specs
        }
        profile.bump_version(now)
        self._students.save(profile)

        state = build_student_state(
            student_id=student_id,
            course_specs=specs,
            events_by_course=events_by_course,
            profile_version=profile.profile_version,
            evidence_version=self._events.evidence_version(student_id),
            now=now,
        )

        snapshots = []
        for course_id, course_state in state["courses"].items():
            for kp, entry in course_state["knowledge"].items():
                snapshots.append({
                    "student_id": student_id,
                    "course_id": course_id,
                    "knowledge_id": kp,
                    "mastery": None if entry["is_prior"] else entry["mastery"],
                    "effective_n": entry["effective_n"],
                    "confidence": entry["confidence"],
                    "n_evidence": entry["n_evidence"],
                    "last_updated": entry["last_updated"],
                    "profile_version": state["profile_version"],
                    "evidence_version": state["evidence_version"],
                })
        self._snapshots.save_all(snapshots)
        return state

    def mastery_snapshots(self, student_id: str,
                          course_id: str | None = None) -> list:
        return self._snapshots.list(student_id, course_id)
