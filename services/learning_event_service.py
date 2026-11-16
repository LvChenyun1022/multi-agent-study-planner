# -*- coding: utf-8 -*-
"""LearningEventService：学习事件的记录、纠正与查询。

纠正语义：
- correct() 插入一条新事件，corrects 指向原事件；原事件保留；
- effective_events() 返回「当前有效」的事件视图：
  被纠正过的原事件与未验证事件不进入画像计算，但仍可在事件流中审计。
"""
from __future__ import annotations

from datetime import datetime, timezone

from schemas.learning_event import (
    EventValidationError, LearningEvent, parse_timestamp,
)
from storage.database import Database
from storage.repositories import LearningEventRepository


class LearningEventService:
    def __init__(self, db: Database):
        self._repo = LearningEventRepository(db)

    def record(self, event_id: str, student_id: str, course_id: str,
               event_type: str, timestamp, knowledge_points=(),
               payload=None, source_id=None, source_type=None,
               verified: bool = True) -> LearningEvent:
        """记录一条学习事件；返回带 seq 的已持久化事件。"""
        event = LearningEvent(
            event_id=event_id,
            student_id=student_id,
            course_id=course_id,
            event_type=event_type,
            timestamp=parse_timestamp(timestamp),
            knowledge_points=tuple(knowledge_points),
            payload=dict(payload or {}),
            source_id=source_id,
            source_type=source_type,
            verified=bool(verified),
        )
        self._repo.append(event)
        return event

    def correct(self, corrects_event_id: str, new_event_id: str,
                new_payload: dict, reason: str,
                timestamp=None) -> LearningEvent:
        """纠正一条已有事件（如分数改判）。

        新事件继承原事件的学生、课程、类型与知识点，
        payload 使用更正后的内容并记录纠正原因。
        """
        original = self._repo.get(corrects_event_id)
        if original is None:
            raise EventValidationError(
                f"被纠正的事件不存在：{corrects_event_id}"
            )
        payload = dict(new_payload)
        payload["correction_reason"] = str(reason)
        event = LearningEvent(
            event_id=new_event_id,
            student_id=original.student_id,
            course_id=original.course_id,
            event_type=original.event_type,
            timestamp=parse_timestamp(
                timestamp if timestamp is not None
                else datetime.now(timezone.utc)
            ),
            knowledge_points=original.knowledge_points,
            payload=payload,
            source_id=original.source_id,
            source_type=original.source_type,
            verified=True,
            corrects=original.event_id,
        )
        self._repo.append(event)
        return event

    def events_for(self, student_id: str, course_id: str | None = None) -> list:
        """完整事件流（含被纠正的原始事件），用于审计与追溯。"""
        return self._repo.list(student_id, course_id)

    def effective_events(self, student_id: str,
                         course_id: str | None = None) -> list:
        """当前有效事件视图：排除已被纠正的原始事件和未验证事件。"""
        events = self._repo.list(student_id, course_id)
        superseded = {e.corrects for e in events if e.corrects}
        return [
            e for e in events
            if e.event_id not in superseded and e.verified
        ]

    def evidence_version(self, student_id: str) -> int:
        return self._repo.latest_seq(student_id)
