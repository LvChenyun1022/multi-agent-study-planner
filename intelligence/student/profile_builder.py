# -*- coding: utf-8 -*-
"""Student Profile Builder：从有效事件流重建学生动态画像。

输出 student_state 字典（纯 JSON 可序列化）：

    {
        "student_id": ...,
        "profile_version": ...,
        "evidence_version": ...,      # 本次计算用到的事件流最大 seq
        "computed_at": ...,
        "courses": {
            "<course_id>": {
                "knowledge": {
                    "<kp>": {
                        "mastery": 0.42,
                        "confidence": "medium",
                        "effective_n": 2.1,
                        "n_evidence": 3,
                        "last_updated": "...",
                        "is_prior": false
                    }, ...
                }
            }, ...
        }
    }

无证据知识点保留中性先验 mastery=0.5 并标记 is_prior=true——
「未知」不等于「不会」，不确定性交由排程引擎的 U 因子表达
（桥接层会把 effective_n 映射为 n_diag）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from schemas.course import CourseSpec
from .mastery_estimator import DEFAULT_HALF_LIFE_DAYS, estimate_mastery

PRIOR_MASTERY = 0.5


def build_student_state(student_id: str, course_specs: list,
                        events_by_course: dict,
                        profile_version: int,
                        evidence_version: int,
                        now: datetime | None = None,
                        half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> dict:
    """重建完整学生画像。

    course_specs: 该学生选课对应的 CourseSpec 列表（已 validate）。
    events_by_course: {course_id: [有效 LearningEvent]}。
    """
    now = now or datetime.now(timezone.utc)
    courses_state: dict = {}
    for spec in course_specs:
        events = events_by_course.get(spec.course_id, [])
        knowledge_state: dict = {}
        for kp in spec.knowledge_weights:
            estimate = estimate_mastery(events, kp, now, half_life_days)
            if estimate.mastery is None:
                knowledge_state[kp] = {
                    "mastery": PRIOR_MASTERY,
                    "confidence": "none",
                    "effective_n": 0.0,
                    "n_evidence": 0,
                    "last_updated": None,
                    "is_prior": True,
                }
            else:
                knowledge_state[kp] = {
                    "mastery": round(estimate.mastery, 6),
                    "confidence": estimate.confidence,
                    "effective_n": round(estimate.effective_n, 6),
                    "n_evidence": estimate.n_evidence,
                    "last_updated": (
                        estimate.last_updated.isoformat()
                        if estimate.last_updated else None
                    ),
                    "is_prior": False,
                }
        courses_state[spec.course_id] = {"knowledge": knowledge_state}

    return {
        "student_id": student_id,
        "profile_version": int(profile_version),
        "evidence_version": int(evidence_version),
        "computed_at": now.isoformat(),
        "courses": courses_state,
    }
