# -*- coding: utf-8 -*-
"""Longitudinal Learning Engine（第一版）：长期保持与遗忘。

对应评审文档的四层能力模型与五态知识状态机：

    即时表现 → 独立掌握 → 长期保持 → 知识迁移
    Unlearned → Acquired → Independently Retrievable → Retained → Transferable

设计纪律：
- long_term_retention 必须建立在实际延迟测试证据上；
  没有延迟测试时输出 unknown，绝不用即时成绩填充；
- 遗忘速率 λ 只在拥有 ≥2 个延迟测量点时才拟合，
  否则如实报告「无法估计」；
- 状态可以降级：延迟证据恶化会把状态打回，
  防止「辅导后短期冲高」被误判为长期掌握；
- 复习优先级 Priority_k = I_k · (1−M_k) · P_forget,k · C_k
  是知识点级信号，交给课程级 scheduler 统一分配时间，
  不与 D/M/W/U 风险重复计分。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from intelligence.assessment.evidence_policy import measurement_kind
from schemas.learning_event import LearningEvent, SCORED_EVENT_TYPES

KNOWLEDGE_STATES = (
    "unlearned",
    "acquired",
    "independently_retrievable",
    "retained",
    "transferable",
)

# 状态判定阈值（默认参数，待校准）
ACQUIRED_MIN = 0.6             # 即时证据达到该表现 → acquired
INDEPENDENT_MIN = 0.7          # 独立证据达到该表现 → independently_retrievable
RETAINED_MIN = 0.6             # 延迟证据达到该表现 → retained
RETAINED_MIN_POINTS = 2        # retained 至少需要的延迟测量点数
TRANSFER_MIN = 0.7             # 迁移证据达到该表现 → transferable
DEMOTE_BELOW = 0.4             # 最新延迟证据低于该值 → 降级

DEFAULT_REVIEW_HORIZON_DAYS = 7.0


@dataclass
class RetentionProfile:
    """一个知识点的纵向保持档案。"""

    knowledge_id: str
    state: str
    forgetting_rate: float | None        # λ（/天）；None 表示无法估计
    retention_unknown: bool              # 无延迟证据时为 True
    delayed_points: int
    transfer_points: int
    last_measurement_at: datetime | None
    notes: list = field(default_factory=list)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


def _days_between(later: datetime, earlier: datetime) -> float:
    return max(0.0, (_naive(later) - _naive(earlier)).total_seconds() / 86400.0)


def _scored_events(events: list, knowledge_id: str) -> list:
    return sorted(
        (
            e for e in events
            if e.event_type in SCORED_EVENT_TYPES
            and knowledge_id in e.knowledge_points
            and e.performance is not None
        ),
        key=lambda e: (_naive(e.timestamp), e.seq or 0),
    )


def fit_forgetting_rate(delayed: list, reference_time: datetime) -> float | None:
    """由延迟测量点拟合个体化遗忘速率 λ（/天）。

    模型：performance(t) ≈ exp(a − λ·t)，对 ln(performance) 做线性回归。
    需要 ≥2 个测量点；任一条件不满足时返回 None（如实报告无法估计）。
    """
    if len(delayed) < 2:
        return None
    points = []
    for event in delayed:
        t = _days_between(event.timestamp, reference_time)
        p = min(max(event.performance, 0.01), 1.0)
        points.append((t, math.log(p)))
    n = len(points)
    mean_t = sum(t for t, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    var_t = sum((t - mean_t) ** 2 for t, _ in points)
    if var_t <= 0:
        return None
    cov = sum((t - mean_t) * (y - mean_y) for t, y in points)
    slope = cov / var_t          # slope = −λ
    return max(0.0, -slope)


def forgetting_probability(forgetting_rate: float | None,
                           horizon_days: float) -> float | None:
    """P_forget = 1 − e^(−λ·horizon)；λ 未知时返回 None。"""
    if forgetting_rate is None:
        return None
    return 1.0 - math.exp(-forgetting_rate * horizon_days)


def classify_knowledge_state(events: list, knowledge_id: str) -> tuple:
    """按五态状态机判定知识点状态；返回 (state, notes)。

    状态可降级：最新延迟证据低于 DEMOTE_BELOW 时，
    无论历史最好状态如何，都打回 acquired 重新复习。
    """
    scored = _scored_events(events, knowledge_id)
    notes: list = []
    if not scored:
        return "unlearned", notes

    by_kind: dict = {}
    for event in scored:
        by_kind.setdefault(measurement_kind(event), []).append(event)

    best = {
        kind: max(e.performance for e in group)
        for kind, group in by_kind.items()
    }
    immediate_best = best.get("immediate", 0.0)
    independent_best = max(best.get("independent", 0.0),
                           best.get("oral", 0.0))
    delayed = by_kind.get("delayed", [])
    transfer = by_kind.get("transfer", [])

    state = "unlearned"
    if immediate_best >= ACQUIRED_MIN or independent_best >= INDEPENDENT_MIN:
        state = "acquired"
    if independent_best >= INDEPENDENT_MIN:
        state = "independently_retrievable"
    if (len(delayed) >= RETAINED_MIN_POINTS
            and best.get("delayed", 0.0) >= RETAINED_MIN):
        state = "retained"
    if transfer and best["transfer"] >= TRANSFER_MIN:
        state = "transferable"

    # 降级规则：最新的延迟/迁移证据显著恶化 → 打回 acquired
    latest_delayed = delayed[-1] if delayed else None
    if latest_delayed is not None and latest_delayed.performance < DEMOTE_BELOW:
        if state in ("retained", "transferable"):
            notes.append(
                f"最新延迟证据 {latest_delayed.performance:.0%} 低于 "
                f"{DEMOTE_BELOW:.0%}，状态由 {state} 降级为 acquired"
            )
            state = "acquired"
    return state, notes


def build_retention_profile(events: list, knowledge_id: str,
                            now: datetime) -> RetentionProfile:
    """汇总一个知识点的纵向保持档案。"""
    scored = _scored_events(events, knowledge_id)
    delayed = [e for e in scored if measurement_kind(e) == "delayed"]
    transfer = [e for e in scored if measurement_kind(e) == "transfer"]
    state, notes = classify_knowledge_state(events, knowledge_id)

    reference_time = scored[0].timestamp if scored else now
    lam = fit_forgetting_rate(delayed, reference_time)
    if not delayed:
        notes.append("无延迟测试证据，长期保持未知（不以即时成绩填充）")
    elif lam is None:
        notes.append("延迟测量点不足或时间跨度过小，遗忘速率无法估计")

    return RetentionProfile(
        knowledge_id=knowledge_id,
        state=state,
        forgetting_rate=lam,
        retention_unknown=not delayed,
        delayed_points=len(delayed),
        transfer_points=len(transfer),
        last_measurement_at=scored[-1].timestamp if scored else None,
        notes=notes,
    )


def review_priority(importance: float, mastery: float,
                    forgetting_rate: float | None,
                    effective_n: float,
                    horizon_days: float = DEFAULT_REVIEW_HORIZON_DAYS,
                    cost_factor: float = 1.0) -> float:
    """知识点级复习优先级：I · (1−M) · P_forget · C。

    λ 未知（无延迟证据）时，P_forget 用证据量兜底：
    证据越少，未知带来的风险越大（P = 1/(1+effective_n)）。
    """
    importance = min(max(float(importance), 0.0), 1.0)
    mastery = min(max(float(mastery), 0.0), 1.0)
    p_forget = forgetting_probability(forgetting_rate, horizon_days)
    if p_forget is None:
        p_forget = 1.0 / (1.0 + max(0.0, effective_n))
    return importance * (1.0 - mastery) * p_forget * max(0.0, cost_factor)
