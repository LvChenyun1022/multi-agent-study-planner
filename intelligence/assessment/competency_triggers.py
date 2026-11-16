# -*- coding: utf-8 -*-
"""Competency Triggers：不确定时才安排测评（动态测试策略）。

对应评审文档 §五 的触发机制：

    Trigger_k = 1  当且仅当满足以下任一条件：
    - U_k > τ_U            独立证据不足（有效样本量过低）
    - |A_k − M_k| > τ_D    作业表现与独立能力明显背离
                           （AI 辅助作业污染画像的主要信号）
    - t − t_last > τ_T     距上次独立测评过久

设计纪律：
- 纯确定性规则，不调用 LLM；
- 输出的是「建议安排一次独立速测」及原因，是否执行由上层决定；
- 触发不代表怀疑学生作弊——它只说明现有证据不足以支撑画像结论。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from schemas.learning_event import LearningEvent, SCORED_EVENT_TYPES
from .evidence_policy import HOMEWORK_EVENT_TYPES, is_independent_evidence

# 默认阈值（待真实数据校准，同 evidence_policy 的校准纪律）
DEFAULT_TAU_EFFECTIVE_N = 1.5     # τ_U：独立证据有效样本量下限
DEFAULT_TAU_DIVERGENCE = 0.25     # τ_D：作业/独立表现背离阈值
DEFAULT_TAU_STALE_DAYS = 14.0     # τ_T：独立证据过期天数


@dataclass
class AssessmentRecommendation:
    """对单个知识点的测评建议。"""

    knowledge_id: str
    triggered: bool
    reasons: list = field(default_factory=list)
    homework_performance: float | None = None      # A_k
    independent_performance: float | None = None   # M_k（独立证据均值）
    days_since_last_independent: float | None = None


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


def _weighted_mean(events: list) -> float | None:
    """申报独立性加权后的平均表现；无证据返回 None。"""
    if not events:
        return None
    from intelligence.assessment.evidence_policy import independence_of
    weights = [independence_of(e) for e in events]
    total = sum(weights)
    if total <= 0:
        return None
    return sum(w * e.performance for w, e in zip(weights, events)) / total


def recommend_assessment(events: list, knowledge_id: str, now: datetime,
                         effective_n: float,
                         tau_effective_n: float = DEFAULT_TAU_EFFECTIVE_N,
                         tau_divergence: float = DEFAULT_TAU_DIVERGENCE,
                         tau_stale_days: float = DEFAULT_TAU_STALE_DAYS
                         ) -> AssessmentRecommendation:
    """判断一个知识点当前是否需要安排独立测评。

    events: 该知识点的有效事件（effective_events 视图）；
    effective_n: 掌握度引擎算出的有效样本量（复用，不重复计算）。
    """
    scored = [
        e for e in events
        if e.event_type in SCORED_EVENT_TYPES
        and knowledge_id in e.knowledge_points
        and e.performance is not None
    ]
    homework = [e for e in scored if e.event_type in HOMEWORK_EVENT_TYPES]
    independent = [e for e in scored if is_independent_evidence(e)]

    result = AssessmentRecommendation(knowledge_id=knowledge_id,
                                      triggered=False)

    # 条件一：独立证据不足
    if effective_n < tau_effective_n:
        result.reasons.append(
            f"独立证据不足（有效样本量 {effective_n:.2f} < {tau_effective_n}）"
        )

    # 条件二：作业表现与独立能力背离
    result.homework_performance = _weighted_mean(homework)
    result.independent_performance = _weighted_mean(independent)
    if (result.homework_performance is not None
            and result.independent_performance is not None):
        gap = abs(result.homework_performance
                  - result.independent_performance)
        if gap > tau_divergence:
            result.reasons.append(
                f"作业表现与独立能力背离（差距 {gap:.2f} > {tau_divergence}）"
            )

    # 条件三：独立证据过期
    if independent:
        latest = max(_naive(e.timestamp) for e in independent)
        days = (_naive(now) - latest).total_seconds() / 86400.0
        result.days_since_last_independent = round(days, 3)
        if days > tau_stale_days:
            result.reasons.append(
                f"距上次独立测评 {days:.0f} 天（> {tau_stale_days} 天）"
            )

    result.triggered = bool(result.reasons)
    return result
