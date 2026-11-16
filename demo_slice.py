# -*- coding: utf-8 -*-
"""ExamOS 2.0 Phase 1 纵向切片演示。

一条命令跑通：

    python demo_slice.py

流程：登记课程档案 → 记录学习事件（含一次分数纠正）→
重建学生画像（版本可溯）→ 桥接生成 exam_plan →
调用 1.0 确定性排程引擎生成考试复习计划。

演示数据全部为【模拟】数据，不代表真实学生记录。
"""
from datetime import datetime, timedelta, timezone

from agents.scheduler_adapter import run_exam_plan
from intelligence.decision.exam_plan_bridge import build_exam_plan
from schemas.course import CourseSpec, TaskSpec
from services.learning_event_service import LearningEventService
from services.student_service import StudentService
from storage.database import Database

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


def banner(text):
    print("\n" + "=" * 56)
    print(f"  {text}")
    print("=" * 56)


def main():
    db = Database(":memory:")
    students = StudentService(db)
    events = LearningEventService(db)

    banner("第一步：登记学生与课程档案")
    students.get_or_create("s1", display_name="演示学生")
    specs = [
        CourseSpec(
            course_id="calculus_001", name="高等数学", exam_day=4,
            credits=5.0, degree=True, priority=0.9,
            knowledge_weights={"multiple_integrals": 0.5, "series": 0.3,
                               "limits": 0.2},
            tasks=[
                TaskSpec("重积分专题", "multiple_integrals", 3.0, True),
                TaskSpec("级数判敛", "series", 2.0, True),
                TaskSpec("极限回顾", "limits", 1.0, False),
            ],
        ).validate(),
        CourseSpec(
            course_id="algebra_001", name="线性代数", exam_day=6,
            credits=3.0, degree=True, priority=0.6,
            knowledge_weights={"eigen": 0.6, "quadratic_form": 0.4},
            tasks=[
                TaskSpec("特征值专题", "eigen", 2.5, True),
                TaskSpec("二次型整理", "quadratic_form", 1.5, False),
            ],
        ).validate(),
    ]
    for spec in specs:
        students.register_course("s1", spec)
        print(f"  已登记：{spec.name}（{spec.exam_day} 天后考试，"
              f"{len(spec.tasks)} 个任务）")

    banner("第二步：记录学习事件（【模拟】数据）")
    quiz_scores = [("mi_quiz_0", 6, 3), ("mi_quiz_1", 8, 2), ("mi_quiz_2", 7, 1)]
    for event_id, score, days_ago in quiz_scores:
        events.record(
            event_id=event_id, student_id="s1", course_id="calculus_001",
            event_type="quiz_submitted",
            timestamp=NOW - timedelta(days=days_ago),
            knowledge_points=("multiple_integrals",),
            payload={"score": score, "total_score": 20},
            source_id=event_id, source_type="quiz",
        )
        print(f"  重积分测验：{score}/20（{days_ago} 天前）")

    events.record(
        event_id="series_quiz_0", student_id="s1", course_id="calculus_001",
        event_type="quiz_submitted", timestamp=NOW - timedelta(days=1),
        knowledge_points=("series",),
        payload={"score": 12, "total_score": 20},
        source_id="series_quiz_0", source_type="quiz",
    )
    print("  级数测验：12/20（1 天前）")

    # 演示纠正语义：第一次重积分测验改判 6 → 10 分
    events.correct(
        "mi_quiz_0", "mi_quiz_0_fix",
        new_payload={"score": 10, "total_score": 20},
        reason="教师复核改判", timestamp=NOW - timedelta(hours=12),
    )
    print("  改判事件：mi_quiz_0 由 6 分纠正为 10 分（原记录保留可审计）")

    banner("第三步：重建学生画像（Student Modeling Engine）")
    state = students.rebuild_state("s1", now=NOW)
    print(f"  profile_version = {state['profile_version']}，"
          f"evidence_version = {state['evidence_version']}")
    for course_id, course_state in state["courses"].items():
        print(f"  [{course_id}]")
        for kp, entry in course_state["knowledge"].items():
            tag = "先验（无证据）" if entry["is_prior"] else (
                f"{entry['n_evidence']} 条证据，置信度 {entry['confidence']}"
            )
            print(f"    {kp:<20} 掌握度 {entry['mastery']:.2f}   {tag}")

    banner("第四步：桥接生成 exam_plan 并调用 1.0 排程引擎")
    plan = build_exam_plan(
        state, specs, daily_hours={0: 3.0, 1: 3.0, 2: 3.0, 3: 3.0}
    )
    for course in plan["courses"]:
        print(f"  {course['name']}：n_diag={course['n_diag']}，知识点掌握度 "
              + ", ".join(f"{k['name']}={k['mastery']:.2f}"
                          for k in course["knowledge"]))

    result = run_exam_plan(plan)
    print()
    print(result["report"])

    banner("演示结束")
    print("  纵向切片闭环：事件 → 画像 → 桥接 → 确定性排程")
    print("  1.0 兼容契约：run_exam_plan() 输入格式未做任何修改")


if __name__ == "__main__":
    main()
