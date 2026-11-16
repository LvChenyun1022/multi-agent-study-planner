# -*- coding: utf-8 -*-
"""LearningEvent：持续学习事件（ExamOS 2.0 Phase 1 核心实体）。

事件溯源纪律：
- 原始事件只追加，不覆盖、不删除（存储层用触发器强制）；
- 成绩或评估纠正通过一条新事件完成，新事件的 ``corrects``
  指向被纠正事件，原始记录永久保留；
- 掌握度等派生状态永远可以由事件流重算，因此可以回答
  「为什么昨天掌握度是 35%，今天变成 48%」。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

EVENT_TYPES = frozenset({
    "study_started",
    "study_completed",
    "assignment_submitted",
    "quiz_submitted",
    "exam_completed",
    "document_uploaded",
    "tutor_interaction",
    "task_overdue",
    "self_assessment",
    # Phase 1.5：能力验证类事件（无辅助独立测评）
    "diagnostic_quiz_submitted",
    "oral_verification_completed",
    # Phase 1.6：纵向保持类事件
    "delayed_test_completed",
    "transfer_test_completed",
})

# 带有分数证据、可用于掌握度估计的事件类型
SCORED_EVENT_TYPES = frozenset({
    "assignment_submitted",
    "quiz_submitted",
    "exam_completed",
    "self_assessment",
    "diagnostic_quiz_submitted",
    "oral_verification_completed",
    "delayed_test_completed",
    "transfer_test_completed",
})

# 学生主动申报的 AI 辅助程度（payload.assistance）。
# 使用 AI 不是负面行为；该字段只用于校准证据的独立性权重 V。
ASSISTANCE_LEVELS = frozenset({
    "none",              # 明确独立完成
    "declared_partial",  # 申报部分使用 AI
    "declared_heavy",    # 申报大量依赖 AI
    "unknown",           # 未申报（默认）
})


class EventValidationError(ValueError):
    """学习事件缺字段、类型错误或取值不合法。"""


def parse_timestamp(value: Any, field_name: str = "timestamp") -> datetime:
    """把 ISO 8601 字符串或 datetime 统一解析为 datetime。"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(f"{field_name} 必须是 ISO 8601 时间字符串")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise EventValidationError(
            f"{field_name} 不是合法的 ISO 8601 时间：{value!r}"
        ) from None


def _require_text(value: Any, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise EventValidationError(f"{field_name} 不能为空")
    return text


@dataclass
class LearningEvent:
    """一条不可变的学习事件。``seq`` 由存储层追加时分配。"""

    event_id: str
    student_id: str
    course_id: str
    event_type: str
    timestamp: datetime
    knowledge_points: tuple = ()
    payload: dict = field(default_factory=dict)
    source_id: str | None = None
    source_type: str | None = None
    verified: bool = True
    corrects: str | None = None
    seq: int | None = None

    def validate(self) -> "LearningEvent":
        self.event_id = _require_text(self.event_id, "event_id")
        self.student_id = _require_text(self.student_id, "student_id")
        self.course_id = _require_text(self.course_id, "course_id")
        if self.event_type not in EVENT_TYPES:
            raise EventValidationError(
                f"未知事件类型：{self.event_type!r}，合法类型见 EVENT_TYPES"
            )
        self.timestamp = parse_timestamp(self.timestamp)
        if not isinstance(self.knowledge_points, (list, tuple)):
            raise EventValidationError("knowledge_points 必须是数组")
        self.knowledge_points = tuple(
            _require_text(kp, "knowledge_points 元素") for kp in self.knowledge_points
        )
        if not isinstance(self.payload, dict):
            raise EventValidationError("payload 必须是对象")
        if self.event_type in SCORED_EVENT_TYPES:
            score = self.payload.get("score")
            total = self.payload.get("total_score")
            if score is None or total is None:
                raise EventValidationError(
                    f"{self.event_type} 事件的 payload 必须包含 score 和 total_score"
                )
            if isinstance(score, bool) or isinstance(total, bool):
                raise EventValidationError("score 和 total_score 必须是数字")
            try:
                score = float(score)
                total = float(total)
            except (TypeError, ValueError):
                raise EventValidationError("score 和 total_score 必须是数字") from None
            if total <= 0:
                raise EventValidationError("total_score 必须大于 0")
            if not 0.0 <= score <= total:
                raise EventValidationError(
                    f"score（{score}）必须位于 [0, total_score（{total}）]"
                )
        if "assistance" in self.payload:
            assistance = self.payload["assistance"]
            if assistance not in ASSISTANCE_LEVELS:
                raise EventValidationError(
                    "payload.assistance 必须是 "
                    f"{sorted(ASSISTANCE_LEVELS)} 之一，收到：{assistance!r}"
                )
        return self

    @property
    def assistance(self) -> str:
        """学生申报的 AI 辅助程度；未申报按 unknown 处理。"""
        return self.payload.get("assistance", "unknown")

    @property
    def performance(self) -> float | None:
        """得分率 evidence y_j ∈ [0,1]；非计分事件返回 None。"""
        if self.event_type not in SCORED_EVENT_TYPES:
            return None
        return float(self.payload["score"]) / float(self.payload["total_score"])

    def to_row(self) -> dict:
        return {
            "event_id": self.event_id,
            "student_id": self.student_id,
            "course_id": self.course_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "knowledge_points": json.dumps(
                list(self.knowledge_points), ensure_ascii=False
            ),
            "payload": json.dumps(self.payload, ensure_ascii=False),
            "source_id": self.source_id,
            "source_type": self.source_type,
            "verified": 1 if self.verified else 0,
            "corrects": self.corrects,
        }

    @classmethod
    def from_row(cls, row: dict) -> "LearningEvent":
        return cls(
            event_id=row["event_id"],
            student_id=row["student_id"],
            course_id=row["course_id"],
            event_type=row["event_type"],
            timestamp=parse_timestamp(row["timestamp"]),
            knowledge_points=tuple(json.loads(row["knowledge_points"])),
            payload=json.loads(row["payload"]),
            source_id=row["source_id"],
            source_type=row["source_type"],
            verified=bool(row["verified"]),
            corrects=row["corrects"],
            seq=row["seq"],
        )
