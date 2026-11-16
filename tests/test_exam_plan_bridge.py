# -*- coding: utf-8 -*-
"""纵向切片端到端测试：

课程资料登记 → 记录学习事件 → 重建学生画像 →
桥接生成 exam_plan → 调用 1.0 run_exam_plan() 排程。

同时验证 2.0 数据层不会破坏 1.0 的兼容契约。
"""
import unittest
from datetime import datetime, timedelta, timezone

from agents.scheduler_adapter import run_exam_plan
from intelligence.decision.exam_plan_bridge import build_exam_plan
from schemas.course import CourseSpec, TaskSpec
from services.learning_event_service import LearningEventService
from services.student_service import StudentService
from storage.database import Database

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


def make_specs():
    calculus = CourseSpec(
        course_id="calculus_001",
        name="高等数学",
        exam_day=4,
        credits=5.0,
        degree=True,
        priority=0.9,
        knowledge_weights={"multiple_integrals": 0.5, "series": 0.3,
                           "limits": 0.2},
        tasks=[
            TaskSpec(name="重积分专题", kp="multiple_integrals", hours=3.0,
                     required=True),
            TaskSpec(name="级数判敛", kp="series", hours=2.0, required=True),
            TaskSpec(name="极限回顾", kp="limits", hours=1.0, required=False),
        ],
    ).validate()
    algebra = CourseSpec(
        course_id="algebra_001",
        name="线性代数",
        exam_day=6,
        credits=3.0,
        degree=True,
        priority=0.6,
        knowledge_weights={"eigen": 0.6, "quadratic_form": 0.4},
        tasks=[
            TaskSpec(name="特征值专题", kp="eigen", hours=2.5, required=True),
            TaskSpec(name="二次型整理", kp="quadratic_form", hours=1.5,
                     required=False),
        ],
    ).validate()
    return [calculus, algebra]


class ExamOSSliceTestCase(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.students = StudentService(self.db)
        self.events = LearningEventService(self.db)
        self.specs = make_specs()
        self.students.get_or_create("s1", display_name="演示学生")
        for spec in self.specs:
            self.students.register_course("s1", spec)

    def tearDown(self):
        self.db.close()

    def _record_weak_calculus_evidence(self):
        # 重积分连续表现不佳（真实弱项）；级数一次中等表现
        for index, score in enumerate((6, 8, 7)):
            self.events.record(
                event_id=f"mi_quiz_{index}",
                student_id="s1",
                course_id="calculus_001",
                event_type="quiz_submitted",
                timestamp=NOW - timedelta(days=3 - index),
                knowledge_points=("multiple_integrals",),
                payload={"score": score, "total_score": 20},
                source_id=f"mi_quiz_{index}",
                source_type="quiz",
            )
        self.events.record(
            event_id="series_quiz_0",
            student_id="s1",
            course_id="calculus_001",
            event_type="quiz_submitted",
            timestamp=NOW - timedelta(days=1),
            knowledge_points=("series",),
            payload={"score": 12, "total_score": 20},
            source_id="series_quiz_0",
            source_type="quiz",
        )

    def test_state_rebuild_flows_into_exam_plan(self):
        self._record_weak_calculus_evidence()
        state = self.students.rebuild_state("s1", now=NOW)

        # 画像：重积分低掌握（有证据），极限保持先验，线代全无证据
        knowledge = state["courses"]["calculus_001"]["knowledge"]
        mi_mastery = knowledge["multiple_integrals"]["mastery"]
        self.assertLess(mi_mastery, 0.45)
        self.assertFalse(knowledge["multiple_integrals"]["is_prior"])
        self.assertTrue(knowledge["limits"]["is_prior"])
        self.assertTrue(
            state["courses"]["algebra_001"]["knowledge"]["eigen"]["is_prior"]
        )

        plan = build_exam_plan(
            state, self.specs, daily_hours={0: 3.0, 1: 3.0, 2: 3.0, 3: 3.0}
        )
        calc = next(c for c in plan["courses"] if c["name"] == "高等数学")
        algebra = next(c for c in plan["courses"] if c["name"] == "线性代数")

        # 掌握度原样流入排程输入；n_diag 反映证据量
        mi = next(k for k in calc["knowledge"]
                  if k["name"] == "multiple_integrals")
        self.assertAlmostEqual(mi["mastery"], mi_mastery, places=6)
        self.assertEqual(calc["n_diag"], 4)   # 4 条独立证据
        self.assertEqual(algebra["n_diag"], 0)

        result = run_exam_plan(plan)
        data = result["data"]
        self.assertEqual(data["mode"], "v1")
        self.assertTrue(data["conservation_ok"])
        # 证据驱动的直接后果：高数掌握缺口 M 必须大于线代
        #（不直接比较综合风险 R：线代无任何证据时 U 因子为最大值，
        #  U 反映「未知」而非「薄弱」，两个维度不应混在一起断言）
        self.assertGreater(
            data["risk_scores_initial"]["高等数学"]["M"],
            data["risk_scores_initial"]["线性代数"]["M"],
        )
        # 同时验证 U 因子语义：线代无证据 → U 达到最大值 1.0
        self.assertAlmostEqual(
            data["risk_scores_initial"]["线性代数"]["U"], 1.0, places=6
        )
        # 必修任务必须出现在计划里
        scheduled_tasks = {row["task"] for row in data["schedule_final"]}
        self.assertIn("重积分专题", scheduled_tasks)
        self.assertIn("特征值专题", scheduled_tasks)

    def test_empty_evidence_still_produces_valid_plan(self):
        state = self.students.rebuild_state("s1", now=NOW)
        plan = build_exam_plan(
            state, self.specs, daily_hours={0: 3.0, 1: 3.0, 2: 3.0, 3: 3.0}
        )
        for course in plan["courses"]:
            self.assertEqual(course["n_diag"], 0)
            for kp in course["knowledge"]:
                self.assertEqual(kp["mastery"], 0.5)  # 全部中性先验
        result = run_exam_plan(plan)
        self.assertTrue(result["data"]["conservation_ok"])

    def test_replan_with_feedback_through_bridge(self):
        self._record_weak_calculus_evidence()
        state = self.students.rebuild_state("s1", now=NOW)
        plan = build_exam_plan(
            state, self.specs,
            daily_hours={0: 3.0, 1: 3.0, 2: 3.0, 3: 3.0},
        )
        plan["execution_feedback"] = {
            "day": 0,
            "completion": {"高等数学": 0.5},
            "default_rate": 1.0,
        }
        result = run_exam_plan(plan)
        data = result["data"]
        self.assertEqual(data["mode"], "v2")
        self.assertTrue(data["conservation_ok"])
        self.assertTrue(data["feedback"])  # 有回补明细

    def test_legacy_weekly_planner_untouched(self):
        # 2.0 数据层的引入不影响 1.0 周计划模式（无 exam_plan 输入）
        from agents.planner_agent import PlannerAgent

        class FakeMCP:
            def call_tool(self, name, args):
                if name == "check_calendar":
                    return {"ok": True, "result": {"events": []}}
                return {"ok": True, "result": {
                    "weather": "晴", "temperature": "20~25°C",
                    "suitable_for_outdoor": True,
                }}

        class FakeHarness:
            @staticmethod
            def is_sensitive(name):
                return False

        class FakeLLM:
            def chat(self, system, user):
                return "排课思路：模拟 LLM 输出"

        agent = PlannerAgent(FakeMCP(), object(), FakeHarness(), llm=FakeLLM())
        result = agent.run({"user_profile": {"薄弱学科": "数学分析"}})
        self.assertEqual(result["planner_mode"], "weekly_heuristic")
        self.assertIn("一周学习计划表", result["output"])


if __name__ == "__main__":
    unittest.main()
