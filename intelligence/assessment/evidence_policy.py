# -*- coding: utf-8 -*-
"""Evidence Policy：证据分级与权重的单一真源。

对应 2.0 评审文档的证据融合模型：

    w_j = R_j · I_j · T_j · V_j

    R_j  证据可靠性（测量方式有多可信）
    I_j  与知识点的相关程度（Phase 2 接入课程图谱前恒为 1.0）
    T_j  时间衰减（mastery_estimator 的 recency，本模块不重复实现）
    V_j  独立性及验证程度（AI 辅助申报的折扣系数）

⚠️ 校准纪律：本文件所有数值都是**默认参数**，未经真实数据校准，
不得作为正式生产参数对外宣称精度。调整权重只改这里，
掌握度引擎与触发器不得各自另写一份。
"""
from __future__ import annotations

from schemas.learning_event import LearningEvent

# ---------------------------------------------------------------- R：证据可靠性
# 按测量方式分级：独立限时/监考类 > 平时测验 > 作业 > 自评
RELIABILITY_BY_EVENT_TYPE = {
    "exam_completed": 1.2,              # 教师监考考试：强证据
    "delayed_test_completed": 1.1,      # 延迟测试：保持力的直接证据
    "transfer_test_completed": 1.1,     # 迁移/变式测试
    "diagnostic_quiz_submitted": 1.05,  # 无辅助独立速测
    "quiz_submitted": 1.0,              # 平时测验
    "oral_verification_completed": 0.9, # 口头/文字过程验证
    "assignment_submitted": 0.8,        # 作业（独立性未知）
    "community_contribution": 0.6,      # 社区证据：经授权与复核但仍属弱测量
    "self_assessment": 0.3,             # 学生自评：辅助证据
}

# source_type 声明优先于事件类型（更具体的信息）
RELIABILITY_BY_SOURCE_TYPE = {
    "exam": 1.2,
    "mock_exam": 1.1,
    "diagnostic_quiz": 1.05,
    "quiz": 1.0,
    "oral_verification": 0.9,
    "assignment": 0.8,
    "self_assessment": 0.3,
}

DEFAULT_RELIABILITY = 0.5

# ---------------------------------------------------------------- V：独立性系数
# 学生主动申报 AI 辅助程度时的证据折扣。
# 纪律：使用 AI 不是负面行为，V 只回答一个问题——
# 「这份证据能在多大程度上代表学生的独立能力」。
ASSISTANCE_MULTIPLIER = {
    "none": 1.0,
    "unknown": 0.9,
    "declared_partial": 0.5,
    "declared_heavy": 0.15,
}

# ---------------------------------------------------------------- 测量方式分类
# 四层能力证据（Performance / Mastery / Retention / Transfer）
MEASUREMENT_KIND_BY_EVENT_TYPE = {
    "assignment_submitted": "immediate",
    "quiz_submitted": "immediate",
    "self_assessment": "immediate",
    "exam_completed": "independent",
    "diagnostic_quiz_submitted": "independent",
    "oral_verification_completed": "oral",
    "delayed_test_completed": "delayed",
    "transfer_test_completed": "transfer",
}

# 作业类证据（用于「作业表现 vs 独立能力」背离检测）
HOMEWORK_EVENT_TYPES = frozenset({"assignment_submitted"})
# 独立能力类证据（用于掌握度结论的主要依据）
INDEPENDENT_EVENT_TYPES = frozenset({
    "exam_completed",
    "diagnostic_quiz_submitted",
    "delayed_test_completed",
    "transfer_test_completed",
    "oral_verification_completed",
})


def reliability_of(event: LearningEvent) -> float:
    """R_j：先看 source_type 声明，再看事件类型，最后取默认值。"""
    if event.source_type in RELIABILITY_BY_SOURCE_TYPE:
        return RELIABILITY_BY_SOURCE_TYPE[event.source_type]
    return RELIABILITY_BY_EVENT_TYPE.get(event.event_type,
                                         DEFAULT_RELIABILITY)


def independence_of(event: LearningEvent) -> float:
    """V_j：AI 辅助申报对应的独立性折扣系数。"""
    return ASSISTANCE_MULTIPLIER[event.assistance]


def measurement_kind(event: LearningEvent) -> str:
    """该证据测量的是哪一层能力。

    社区证据（community_contribution）的测量层级由 payload
    显式声明（如 knowledge_transfer → transfer），其余事件按类型映射。
    """
    if event.event_type == "community_contribution":
        declared = event.payload.get("measurement_kind")
        if declared in ("immediate", "independent", "oral", "delayed",
                        "transfer"):
            return declared
        return "immediate"
    return MEASUREMENT_KIND_BY_EVENT_TYPE.get(event.event_type, "immediate")


def is_independent_evidence(event: LearningEvent) -> bool:
    """是否为独立能力证据（含明确的独立完成申报）。"""
    return (
        event.event_type in INDEPENDENT_EVENT_TYPES
        or event.assistance == "none"
    )
