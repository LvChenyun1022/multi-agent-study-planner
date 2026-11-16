# -*- coding: utf-8 -*-
"""Evidence Mapper：社区证据 → 统一学习证据库（LearningEvent）。

三条不可绕过的纪律（对应社区方案 §三、§八）：

1. 浏览、点赞、收藏永不进入能力画像——
   MASTERY_EXCLUDED_BEHAVIORS 中的行为没有证据强度，
   本模块拒绝为它们生成任何能力证据；
2. 未授权不分析——consent=False 的证据直接丢弃；
3. 有争议不进画像——pending / disputed 的证据在复核前丢弃，
   学生对 AI 判断的异议权优先于系统的分析结论。

转换后的 LearningEvent 复用既有权重体系：
- event_type = community_contribution（R = 0.6，弱于正式测评）；
- 测量层级由证据类型映射（迁移/延迟解释分别进入
  transfer / delayed 层，供纵向引擎使用）；
- verified 标志承载复核语义，与事件溯源纪律一致。
"""
from __future__ import annotations

from datetime import datetime, timezone

from schemas.community import CommunityEvidence
from schemas.learning_event import EventValidationError, LearningEvent

# 隐私红线：这些行为只可用于内容推荐，永不进入能力画像
MASTERY_EXCLUDED_BEHAVIORS = frozenset({"view", "like", "collect"})

# 社区行为 → 证据强度（默认 confidence 参考值，待真实数据校准）
# 对应评审文档的行为信息量表：提问中等，解题/回答/纠错较强，
# 变式追问强，延迟后再次正确解释很强。
BEHAVIOR_STRENGTH = {
    "post_question": 0.4,
    "post_solution": 0.7,
    "answer": 0.7,
    "correct_error": 0.75,
    "answer_variant": 0.85,
    "delayed_re_explain": 0.95,
}

# 证据类型 → 测量层级（供 retention 引擎分层使用）
MEASUREMENT_KIND_BY_EVIDENCE_TYPE = {
    "problem_identification": "immediate",
    "knowledge_expression": "immediate",
    "knowledge_application": "immediate",
    "error_diagnosis": "immediate",
    "collaboration": "immediate",
    "knowledge_transfer": "transfer",
}

# 可进入画像的复核状态
_PASSING_REVIEW_STATUSES = frozenset({"not_required", "approved"})


def behavior_strength(behavior: str) -> float | None:
    """社区行为的证据强度；被排除行为返回 None。"""
    if behavior in MASTERY_EXCLUDED_BEHAVIORS:
        return None
    return BEHAVIOR_STRENGTH.get(behavior)


def is_behavior_usable_for_mastery(behavior: str) -> bool:
    return behavior_strength(behavior) is not None


def evidence_to_event(evidence: CommunityEvidence, event_id: str,
                      course_id: str, timestamp=None,
                      measurement_kind: str | None = None
                      ) -> LearningEvent | None:
    """把一条社区证据转换为可入库的 LearningEvent。

    返回 None 表示证据被门禁拦截（未授权或未过复核）——
    这是正常路径，不是异常。
    """
    evidence.validate()
    if not evidence.consent:
        return None
    if evidence.human_review_status not in _PASSING_REVIEW_STATUSES:
        return None

    correctness = evidence.evaluation.get("correctness")
    if correctness is None:
        raise EventValidationError(
            f"证据 {evidence.evidence_id} 缺少 evaluation.correctness，"
            "无法转换为计分事件"
        )
    kind = measurement_kind or MEASUREMENT_KIND_BY_EVIDENCE_TYPE.get(
        evidence.evidence_type, "immediate"
    )
    event = LearningEvent(
        event_id=event_id,
        student_id=evidence.student_id,
        course_id=course_id,
        event_type="community_contribution",
        timestamp=timestamp or datetime.now(timezone.utc),
        knowledge_points=tuple(evidence.knowledge_points),
        payload={
            "score": round(float(correctness) * 20, 6),
            "total_score": 20,
            "assistance": "unknown",
            "measurement_kind": kind,
            "community_evidence_id": evidence.evidence_id,
            "community_evidence_type": evidence.evidence_type,
            "community_confidence": evidence.confidence,
        },
        source_id=evidence.post_id,
        source_type="community",
        verified=True,
    )
    return event.validate()
