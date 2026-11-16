# -*- coding: utf-8 -*-
"""Student Modeling Engine（第一版）：证据加权的知识掌握度估计。

对应 2.0 升级方案 §4.1：

    M̂_ik(t) = Σ_j w_j y_j / Σ_j w_j
    w_j = w_reliability · w_recency · w_difficulty

设计纪律：
- 同一来源（source_id，如同一道题）反复练习只算一份证据，
  取最近一次表现，避免「刷同一道题」虚增掌握度与样本量；
- 证据权重含时间衰减（默认半衰期 14 天），近期表现主导估计；
- 输出有效样本量（Kish）与置信等级，让下游知道「这个数有多可信」；
- 纯函数实现，不依赖数据库，可独立测试。

后续版本可替换为 BKT 等知识追踪模型，调用契约保持不变。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from schemas.learning_event import LearningEvent, SCORED_EVENT_TYPES

DEFAULT_HALF_LIFE_DAYS = 14.0

# 证据可靠度：正式考试 > 模拟卷 > 平时测验 > 作业 > 学生自评
# 证据可靠性 R 与独立性 V 的数值统一由
# intelligence/assessment/evidence_policy.py 维护（单一真源）；
# 此处保留同名别名，兼容既有调用与测试。
from intelligence.assessment.evidence_policy import (
    DEFAULT_RELIABILITY,
    RELIABILITY_BY_EVENT_TYPE,
    RELIABILITY_BY_SOURCE_TYPE,
    independence_of,
    measurement_kind,
    reliability_of,
)

# 置信等级阈值（基于有效样本量）
CONFIDENCE_MEDIUM_THRESHOLD = 1.5
CONFIDENCE_HIGH_THRESHOLD = 3.0


@dataclass
class MasteryEstimate:
    """一个知识点上的掌握度估计结果。"""

    knowledge_id: str
    mastery: float | None      # None 表示完全没有证据
    effective_n: float         # Kish 有效样本量
    confidence: str            # none / low / medium / high
    n_evidence: int            # 去重后的证据条数
    last_updated: datetime | None

    @property
    def mastery_or_prior(self) -> float:
        """无证据时回退到中性先验 0.5（由调用方决定是否采用）。"""
        return self.mastery if self.mastery is not None else 0.5


def _naive(dt: datetime) -> datetime:
    """统一去掉时区信息再比较，避免 naive/aware 混用异常。"""
    return dt.replace(tzinfo=None)


def recency_weight(event: LearningEvent, now: datetime,
                   half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    days = max(
        0.0, (_naive(now) - _naive(event.timestamp)).total_seconds() / 86400.0
    )
    return 0.5 ** (days / half_life_days)


def difficulty_weight(event: LearningEvent) -> float:
    """题目越难，答对/答错包含的信息量越大。无难度标注时取 1.0。"""
    difficulty = event.payload.get("difficulty")
    if isinstance(difficulty, bool) or difficulty is None:
        return 1.0
    try:
        difficulty = float(difficulty)
    except (TypeError, ValueError):
        return 1.0
    if not 0.0 <= difficulty <= 1.0:
        return 1.0
    return 0.5 + difficulty


def evidence_weight(event: LearningEvent, now: datetime,
                    half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    """w_j = R_j · V_j · T_j · D_j（可靠性·独立性·时间衰减·难度）。

    V_j（独立性）来自 AI 辅助申报；R/V 数值由 evidence_policy 统一维护。
    """
    return (
        reliability_of(event)
        * independence_of(event)
        * recency_weight(event, now, half_life_days)
        * difficulty_weight(event)
    )


def dedupe_by_source(events: list) -> list:
    """同一 source_id 只保留最新一条（按 timestamp，再按 seq 决胜）。"""
    latest: dict = {}
    result: list = []
    for event in events:
        if not event.source_id:
            result.append(event)
            continue
        key = event.source_id
        current = latest.get(key)
        if current is None or (
            _naive(event.timestamp), event.seq or 0
        ) >= (_naive(current.timestamp), current.seq or 0):
            latest[key] = event
    result.extend(latest.values())
    return result


def confidence_level(effective_n: float) -> str:
    if effective_n <= 0.0:
        return "none"
    if effective_n < CONFIDENCE_MEDIUM_THRESHOLD:
        return "low"
    if effective_n < CONFIDENCE_HIGH_THRESHOLD:
        return "medium"
    return "high"


def estimate_mastery(events: list, knowledge_id: str, now: datetime,
                     half_life_days: float = DEFAULT_HALF_LIFE_DAYS
                     ) -> MasteryEstimate:
    """从有效事件流估计一个知识点的掌握度。

    events 应来自 LearningEventService.effective_events()
    （已排除被纠正与未验证事件）。
    """
    relevant = [
        e for e in events
        if e.event_type in SCORED_EVENT_TYPES
        and knowledge_id in e.knowledge_points
        and e.performance is not None
    ]
    evidence = dedupe_by_source(relevant)
    if not evidence:
        return MasteryEstimate(
            knowledge_id=knowledge_id, mastery=None, effective_n=0.0,
            confidence="none", n_evidence=0, last_updated=None,
        )

    weights = [evidence_weight(e, now, half_life_days) for e in evidence]
    total_w = sum(weights)
    mastery = sum(w * e.performance for w, e in zip(weights, evidence)) / total_w
    # Kish 有效样本量：(Σw)² / Σw²
    effective_n = total_w * total_w / sum(w * w for w in weights)
    last_updated = max(
        evidence, key=lambda e: (_naive(e.timestamp), e.seq or 0)
    ).timestamp

    return MasteryEstimate(
        knowledge_id=knowledge_id,
        mastery=mastery,
        effective_n=effective_n,
        confidence=confidence_level(effective_n),
        n_evidence=len(evidence),
        last_updated=last_updated,
    )
