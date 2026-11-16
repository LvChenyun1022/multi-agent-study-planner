# -*- coding: utf-8 -*-
"""社区数据模型（ExamOS Campus 内核）。

设计纪律（来自社区方案评审文档，逐条落实）：
- 社区引擎不直接给学生打分，只产出有来源、有置信度的
  CommunityEvidence，经授权门禁后进入统一证据库；
- 浏览、点赞、收藏永不进入能力画像（见 evidence_mapper）；
- 学生必须明确授权（consent_analysis）才分析其发言；
- 有争议内容（disputed）在复核前不得写入画像；
- 社区表现只补充学业画像，不能用于推断智力或人格特征。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .learning_event import EventValidationError, parse_timestamp

POST_TYPES = frozenset({
    "question",      # 课程答疑提问
    "experience",    # 学习经验分享
    "discussion",    # 讨论
    "challenge",     # 知识挑战
    "note",          # 笔记
})

# 社区行为类型（原始行为分别保存，不直接变成画像分数）
BEHAVIOR_TYPES = frozenset({
    "view", "like", "collect",
    "post_question", "post_solution", "answer",
    "correct_error", "answer_variant", "delayed_re_explain",
})

# 社区能力证据类型（六维能力画像的证据分类）
EVIDENCE_TYPES = frozenset({
    "problem_identification",  # 问题识别能力
    "knowledge_expression",    # 知识表达能力
    "knowledge_application",   # 知识应用能力
    "error_diagnosis",         # 逻辑辨析能力
    "knowledge_transfer",      # 知识迁移能力
    "collaboration",           # 协作学习能力
})

# 复核状态：只有 not_required / approved 的证据可进入画像
REVIEW_STATUSES = frozenset({
    "not_required", "pending", "approved", "disputed",
})


def _require_text(value, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise EventValidationError(f"{field_name} 不能为空")
    return text


@dataclass
class CommunityPost:
    post_id: str
    author_id: str
    course_id: str
    post_type: str
    content: str
    knowledge_points: tuple = ()
    created_at: object = None
    consent_analysis: bool = False   # 作者是否授权用于画像分析

    def validate(self) -> "CommunityPost":
        self.post_id = _require_text(self.post_id, "post_id")
        self.author_id = _require_text(self.author_id, "author_id")
        self.course_id = _require_text(self.course_id, "course_id")
        if self.post_type not in POST_TYPES:
            raise EventValidationError(f"未知帖子类型：{self.post_type!r}")
        self.knowledge_points = tuple(self.knowledge_points)
        if self.created_at is not None:
            self.created_at = parse_timestamp(self.created_at, "created_at")
        if not isinstance(self.consent_analysis, bool):
            raise EventValidationError("consent_analysis 必须是 true/false")
        return self


@dataclass
class CommunityReply:
    reply_id: str
    post_id: str
    author_id: str
    content: str
    parent_reply_id: str | None = None
    created_at: object = None
    consent_analysis: bool = False

    def validate(self) -> "CommunityReply":
        self.reply_id = _require_text(self.reply_id, "reply_id")
        self.post_id = _require_text(self.post_id, "post_id")
        self.author_id = _require_text(self.author_id, "author_id")
        if self.created_at is not None:
            self.created_at = parse_timestamp(self.created_at, "created_at")
        if not isinstance(self.consent_analysis, bool):
            raise EventValidationError("consent_analysis 必须是 true/false")
        return self


@dataclass
class DiscussionThread:
    """知识讨论链：从提问、讨论、自我修正到独立验证的完整证据链。"""

    thread_id: str
    student_id: str
    course_id: str
    knowledge_points: tuple
    root_post_id: str
    reply_ids: tuple = ()
    verification_event_id: str | None = None   # 延迟验证（如三天后速测）
    status: str = "open"                        # open / verified / closed

    def validate(self) -> "DiscussionThread":
        self.thread_id = _require_text(self.thread_id, "thread_id")
        self.student_id = _require_text(self.student_id, "student_id")
        self.course_id = _require_text(self.course_id, "course_id")
        self.root_post_id = _require_text(self.root_post_id, "root_post_id")
        if not self.knowledge_points:
            raise EventValidationError("讨论链必须关联至少一个知识点")
        self.knowledge_points = tuple(self.knowledge_points)
        self.reply_ids = tuple(self.reply_ids)
        if self.status not in ("open", "verified", "closed"):
            raise EventValidationError(f"未知讨论链状态：{self.status!r}")
        return self


@dataclass
class CommunityEvidence:
    """一条社区能力证据。

    evaluation 各维度分数（correctness / reasoning_quality /
    transfer_quality 等）由上游评估器（LLM 或人工）提供，
    本层只校验结构与门禁，不计算这些分数。
    """

    evidence_id: str
    student_id: str
    post_id: str
    evidence_type: str
    knowledge_points: tuple
    evaluation: dict = field(default_factory=dict)
    confidence: float = 0.5
    source_reply_ids: tuple = ()
    human_review_status: str = "not_required"
    consent: bool = False

    def validate(self) -> "CommunityEvidence":
        self.evidence_id = _require_text(self.evidence_id, "evidence_id")
        self.student_id = _require_text(self.student_id, "student_id")
        self.post_id = _require_text(self.post_id, "post_id")
        if self.evidence_type not in EVIDENCE_TYPES:
            raise EventValidationError(
                f"未知证据类型：{self.evidence_type!r}"
            )
        if not self.knowledge_points:
            raise EventValidationError("证据必须关联至少一个知识点")
        self.knowledge_points = tuple(self.knowledge_points)
        if not isinstance(self.evaluation, dict):
            raise EventValidationError("evaluation 必须是对象")
        correctness = self.evaluation.get("correctness")
        if correctness is not None:
            if isinstance(correctness, bool) or not 0.0 <= float(correctness) <= 1.0:
                raise EventValidationError(
                    "evaluation.correctness 必须位于 [0, 1]"
                )
        if isinstance(self.confidence, bool) or not 0.0 <= float(
            self.confidence
        ) <= 1.0:
            raise EventValidationError("confidence 必须位于 [0, 1]")
        self.source_reply_ids = tuple(self.source_reply_ids)
        if self.human_review_status not in REVIEW_STATUSES:
            raise EventValidationError(
                f"未知复核状态：{self.human_review_status!r}"
            )
        if not isinstance(self.consent, bool):
            raise EventValidationError("consent 必须是 true/false")
        return self
