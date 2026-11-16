# -*- coding: utf-8 -*-
"""StudentProfile：学生基础档案（ExamOS 2.0 Phase 1）。

设计约定（来自 2.0 升级方案）：
- 基础档案与动态学习状态分开保存；本类只保存相对稳定的基础信息；
- learning_preferences 仅记录学生明确表达的偏好，
  不解释为经过科学验证的固定学习类型；
- profile_version 每次画像重建时递增，用于解释画像变化。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .learning_event import EventValidationError, parse_timestamp


@dataclass
class StudentProfile:
    student_id: str
    display_name: str = "Student"
    academic_year: int = 2026
    timezone: str = "Asia/Shanghai"
    learning_preferences: dict = field(default_factory=dict)
    available_time: dict = field(default_factory=dict)
    enrolled_courses: tuple = ()
    profile_version: int = 1
    updated_at: datetime | None = None

    def validate(self) -> "StudentProfile":
        if not str(self.student_id).strip():
            raise EventValidationError("student_id 不能为空")
        if isinstance(self.academic_year, bool) or not isinstance(
            self.academic_year, int
        ):
            raise EventValidationError("academic_year 必须是整数")
        if not isinstance(self.learning_preferences, dict):
            raise EventValidationError("learning_preferences 必须是对象")
        if not isinstance(self.available_time, dict):
            raise EventValidationError("available_time 必须是对象")
        if isinstance(self.profile_version, bool) or self.profile_version < 1:
            raise EventValidationError("profile_version 必须是不小于 1 的整数")
        if self.updated_at is not None:
            self.updated_at = parse_timestamp(self.updated_at, "updated_at")
        self.enrolled_courses = tuple(str(c) for c in self.enrolled_courses)
        return self

    def bump_version(self, now: datetime | None = None) -> None:
        self.profile_version += 1
        self.updated_at = now or datetime.now(timezone.utc)
