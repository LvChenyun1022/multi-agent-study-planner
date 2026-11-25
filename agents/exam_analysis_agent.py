# -*- coding: utf-8 -*-
"""考试分析 Agent：把自然语言考试信息变成可排程的 CourseSpec。

职责边界（对应设计书 5.4「算法与 LLM 的分工边界」）：

- LLM 只做**非结构化文本理解**：从「考试范围」描述中提取知识点、
  复习任务、任务依赖建议，并以严格 JSON 返回；
- 确定性代码负责**一切数值与结构校验**：日期换算（exam_day）、
  字段类型、权重/工时合法性、任务依赖环检测，全部复用
  ``schemas.course.CourseSpec`` 的校验规则——与排程引擎入口
  ``scheduler_adapter`` 完全同源，杜绝"LLM 说了算"；
- LLM 输出一律视为不可信输入：无法解析或校验失败时抛出
  ``ExamAnalysisError``，绝不用猜测值填充。

两种输入模式：

1. 结构化模式（无需 LLM）：course_inputs 已含 knowledge/tasks
   字段，直接校验建档案——单元测试与离线演示走这条路；
2. 文本抽取模式（需真实 LLM）：course_inputs 只含 scope_text，
   由 LLM 抽取知识点与任务。MockLLM 无法产出合法 JSON，
   此时会明确报错而不是静默降级。

本 Agent **不**估计每日可用时间：daily_hours 必须由上游按真实
约束（课表、运动、日历事件）显式提供，这是初赛确立的工程边界。
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from .base_agent import BaseAgent
from schemas.course import CourseSpec, TaskSpec
from schemas.learning_event import EventValidationError


class ExamAnalysisError(ValueError):
    """考试分析输入不合法，或 LLM 抽取结果未通过结构化校验。"""


SYSTEM_PROMPT = (
    "你是考试范围分析助手。从学生给出的考试范围描述中提取结构化复习计划要素。\n"
    "只输出一个 JSON 对象，不要输出任何解释、markdown 标记或其他文字。\n"
    'JSON 结构：{"knowledge": [{"name": "知识点", "weight": 数值}], '
    '"tasks": [{"name": "任务名", "kp": "所属知识点", "hours": 数值, '
    '"required": true或false, "deps": ["前置任务名"]}]}\n'
    "规则：\n"
    "1. weight 为知识点在考试中的相对权重（正数，无需归一化）；\n"
    "2. hours 为该任务的预计复习时长（小时，按 0.5 小时粒度估计）；\n"
    "3. 考试范围核心内容的任务 required 为 true，巩固提高类为 false；\n"
    "4. deps 只引用本课程 tasks 中已出现的任务名，没有依赖则为空数组；\n"
    "5. 每个任务的 kp 必须是 knowledge 中已声明的 name；\n"
    "6. 不确定的数值给出保守估计，不要编造精确数字。"
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict:
    """从 LLM 回复中提取第一个完整 JSON 对象。

    容忍 ```json 代码围栏与前导/尾随空白；除此之外的杂质一律拒绝。
    """
    cleaned = _FENCE_RE.sub("", text).strip()
    decoder = json.JSONDecoder()
    for start, ch in enumerate(cleaned):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ExamAnalysisError(
        "LLM 回复中未找到可解析的 JSON 对象；"
        "若当前使用 MockLLM，请改用结构化输入或接入真实模型"
    )


def _exam_day(exam_date: str | None, exam_day: Any,
              reference: date) -> int:
    """把考试日期换算为相对日序号；日期计算必须由代码完成，不信任 LLM。"""
    if exam_day is not None:
        try:
            day = int(exam_day)
        except (TypeError, ValueError):
            raise ExamAnalysisError("exam_day 必须是整数") from None
        if day < 1:
            raise ExamAnalysisError("exam_day 必须 ≥ 1（考试至少距今一天）")
        return day
    if not exam_date:
        raise ExamAnalysisError("每门课程必须提供 exam_date 或 exam_day")
    try:
        target = date.fromisoformat(str(exam_date).strip())
    except ValueError:
        raise ExamAnalysisError(
            f"exam_date 必须是 YYYY-MM-DD 格式：{exam_date!r}"
        ) from None
    day = (target - reference).days
    if day < 1:
        raise ExamAnalysisError(
            f"考试日期 {target} 不晚于参考日期 {reference}，无法安排复习"
        )
    return day


class ExamAnalysisAgent(BaseAgent):
    """考试分析 Agent：考试信息 → CourseSpec 列表（可直接进桥接层）。"""

    name = "考试分析Agent"

    def run(self, context: dict) -> dict:
        raw_courses = context.get("course_inputs")
        if not isinstance(raw_courses, list) or not raw_courses:
            raise ExamAnalysisError("context.course_inputs 必须是非空数组")

        raw_ref = context.get("reference_date")
        if raw_ref is None:
            reference = date.today()
        else:
            try:
                reference = date.fromisoformat(str(raw_ref).strip())
            except ValueError:
                raise ExamAnalysisError(
                    f"reference_date 必须是 YYYY-MM-DD 格式：{raw_ref!r}"
                ) from None

        specs: list[CourseSpec] = []
        summaries: list[str] = []
        for index, raw in enumerate(raw_courses):
            spec, summary = self._analyze_course(raw, index, reference)
            specs.append(spec)
            summaries.append(summary)

        header = (
            f"【考试分析】共解析 {len(specs)} 门课程"
            f"（参考日期 {reference.isoformat()}），结果已经过结构化校验；"
            "LLM 抽取的工时与权重为保守估计【待确认】："
        )
        return {
            "output": "\n\n".join([header, *summaries]),
            "course_specs": specs,
            "reference_date": reference.isoformat(),
        }

    # ------------------------------------------------------------------
    def _analyze_course(self, raw: Any, index: int,
                        reference: date) -> tuple[CourseSpec, str]:
        prefix = f"course_inputs[{index}]"
        if not isinstance(raw, dict):
            raise ExamAnalysisError(f"{prefix} 必须是 JSON 对象")

        name = str(raw.get("name", "")).strip()
        if not name:
            raise ExamAnalysisError(f"{prefix}.name 不能为空")
        exam_day = _exam_day(raw.get("exam_date"), raw.get("exam_day"), reference)

        if raw.get("knowledge") is not None or raw.get("tasks") is not None:
            knowledge, tasks, source = self._structured_payload(raw, prefix)
        elif raw.get("scope_text"):
            knowledge, tasks, source = self._llm_payload(name, raw, prefix)
        else:
            raise ExamAnalysisError(
                f"{prefix} 必须提供结构化 knowledge/tasks，或提供 scope_text"
            )

        try:
            spec = CourseSpec(
                course_id=str(raw.get("course_id") or f"course-{index + 1}"),
                name=name,
                exam_day=exam_day,
                credits=raw.get("credits", 3.0),
                degree=raw.get("degree", True),
                priority=raw.get("priority", 0.5),
                a=raw.get("a", 2.0),
                b=raw.get("b", 0.5),
                min_hours=raw.get("min_hours", 0.0),
                knowledge_weights=dict(knowledge),
                tasks=[
                    TaskSpec(
                        name=t["name"], kp=t["kp"], hours=t["hours"],
                        required=t["required"], deps=list(t.get("deps", [])),
                    )
                    for t in tasks
                ],
            ).validate()
        except EventValidationError as exc:
            raise ExamAnalysisError(f"{prefix} 校验未通过：{exc}") from None

        total_hours = sum(t["hours"] for t in tasks)
        summary = (
            f"· {name}（考试日 Day {exam_day}，{source}）\n"
            f"  知识点 {len(knowledge)} 个："
            + "、".join(f"{k}(w={v:g})" for k, v in knowledge.items())
            + f"\n  任务 {len(tasks)} 项，合计 {total_hours:g}h："
            + "；".join(
                f"{t['name']} {t['hours']:g}h（{'必修' if t['required'] else '可选'}）"
                for t in tasks
            )
        )
        return spec, summary

    # ------------------------------------------------------------------
    def _structured_payload(self, raw: dict, prefix: str):
        """结构化模式：直接使用调用方给出的 knowledge/tasks，来源标注。"""
        raw_knowledge = raw.get("knowledge")
        raw_tasks = raw.get("tasks")
        if not isinstance(raw_knowledge, dict) or not raw_knowledge:
            raise ExamAnalysisError(f"{prefix}.knowledge 必须是非空对象（知识点→权重）")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ExamAnalysisError(f"{prefix}.tasks 必须是非空数组")
        knowledge = {str(k): v for k, v in raw_knowledge.items()}
        tasks = [dict(t) if isinstance(t, dict) else t for t in raw_tasks]
        if not all(isinstance(t, dict) for t in tasks):
            raise ExamAnalysisError(f"{prefix}.tasks 元素必须是 JSON 对象")
        return knowledge, tasks, "结构化输入"

    def _llm_payload(self, name: str, raw: dict, prefix: str):
        """文本抽取模式：LLM 输出 JSON → 解析 → 字段级白名单过滤。"""
        scope_text = str(raw["scope_text"]).strip()
        hint = str(raw.get("hint", "")).strip()
        user_msg = f"课程：{name}\n考试范围描述：\n{scope_text}"
        if hint:
            user_msg += f"\n补充说明：{hint}"

        reply = self.llm.chat(SYSTEM_PROMPT, user_msg)
        extracted = _extract_json(reply)

        raw_knowledge = extracted.get("knowledge")
        raw_tasks = extracted.get("tasks")
        if not isinstance(raw_knowledge, list) or not raw_knowledge:
            raise ExamAnalysisError(f"{prefix}：LLM 结果的 knowledge 必须是非空数组")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ExamAnalysisError(f"{prefix}：LLM 结果的 tasks 必须是非空数组")

        knowledge: dict[str, Any] = {}
        for k_index, item in enumerate(raw_knowledge):
            if not isinstance(item, dict):
                raise ExamAnalysisError(f"{prefix}：knowledge[{k_index}] 必须是对象")
            kp_name = str(item.get("name", "")).strip()
            if not kp_name:
                raise ExamAnalysisError(f"{prefix}：knowledge[{k_index}].name 不能为空")
            knowledge[kp_name] = item.get("weight")

        tasks = []
        for t_index, item in enumerate(raw_tasks):
            if not isinstance(item, dict):
                raise ExamAnalysisError(f"{prefix}：tasks[{t_index}] 必须是对象")
            tasks.append({
                "name": item.get("name", ""),
                "kp": item.get("kp", ""),
                "hours": item.get("hours"),
                "required": item.get("required"),
                "deps": item.get("deps", []),
            })
        return knowledge, tasks, "LLM 抽取【待确认】"
