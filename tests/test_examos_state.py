# -*- coding: utf-8 -*-
"""examos_state.py 多日推进状态层测试。

覆盖两条不变量：
1. 任务总量守恒：total = done + remaining + 当前计划已分配；
2. 状态推进两路径——触发重规划（退回未来块重排）与未触发簿记
   （未来计划不变）——行为符合设计书 6.2/6.3 的显著性闸门语义。
"""
import unittest
from datetime import datetime, timedelta, timezone

import examos_state as es


T0 = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)


def make_course(course_id, name, exam_day, tasks, **kw):
    base = dict(
        credits=4.0, degree=True, priority=0.67, n_diag=10,
        a=0.8, b=0.2, min_hours=0.0,
        knowledge=[{"name": "KP", "weight": 1.0, "mastery": 0.5,
                    "mastery_source": "中性先验"}],
        task_state={},
    )
    base.update(kw)
    return {
        "course_id": course_id, "name": name, "exam_day": exam_day,
        "tasks": tasks, **base,
    }


def make_state(exam_days=(5, 6)):
    state = es.default_state()
    state["origin_date"] = "2026-11-25"
    state["daily_hours"] = {str(d): 4.0 for d in range(8)}
    state["courses"] = [
        make_course("c1", "线性代数", exam_days[0], [
            {"name": "矩阵运算强化", "kp": "KP", "hours": 2.0,
             "required": True, "deps": []},
            {"name": "特征值专题", "kp": "KP", "hours": 2.0,
             "required": True, "deps": []},
        ]),
        make_course("c2", "大学英语", exam_days[1], [
            {"name": "阅读真题", "kp": "KP", "hours": 4.0,
             "required": True, "deps": []},
        ], credits=3.0, degree=False, priority=0.33, n_diag=20,
            a=0.6, b=0.15),
    ]
    return state


def planned_by_task(state):
    plan = es.current_plan(state)
    out = {}
    for blocks in (plan.get("schedule") or {}).values():
        for b in blocks:
            key = (b["course"], b["task"])
            out[key] = out.get(key, 0.0) + float(b["hours"])
    return out


def assert_conservation(testcase, state):
    """总量 = 已完成 + 剩余未安排 + 当前计划已分配（对每门课每个任务）。"""
    planned = planned_by_task(state)
    for course in state["courses"]:
        for task in course["tasks"]:
            ts = (course.get("task_state") or {}).get(
                task["name"], {"done": 0.0, "remaining": task["hours"]})
            total = float(task["hours"])
            lhs = float(ts["done"]) + float(ts["remaining"])
            lhs += planned.get((course["name"], task["name"]), 0.0)
            testcase.assertAlmostEqual(
                total, lhs, places=5,
                msg=f"{course['name']}/{task['name']} 不守恒："
                    f"total={total}, done={ts['done']}, "
                    f"remaining={ts['remaining']}, "
                    f"planned={planned.get((course['name'], task['name']), 0.0)}",
            )


class TestInitialPlan(unittest.TestCase):
    def test_initial_plan_creates_v1_and_conserves(self):
        state = make_state()
        version = es.initial_plan(state, 0)
        self.assertEqual(version["mode"], "v1")
        self.assertTrue(version["conservation_ok"])
        self.assertIs(es.current_plan(state), version)
        # 每日分配不超过日预算
        for day, blocks in version["schedule"].items():
            total = sum(b["hours"] for b in blocks)
            self.assertLessEqual(total, state["daily_hours"][day] + 1e-9)
        assert_conservation(self, state)

    def test_initial_plan_rejects_empty_courses(self):
        state = es.default_state()
        state["daily_hours"] = {"0": 4.0}
        with self.assertRaises(es.StateError):
            es.initial_plan(state, 0)


class TestCheckin(unittest.TestCase):
    def test_below_threshold_bookkeeps_without_replan(self):
        state = make_state()
        es.initial_plan(state, 0)
        day0 = es.plan_blocks(es.current_plan(state), 0)
        self.assertTrue(day0)
        completion = {b["course"]: 0.9 for b in day0}
        outcome = es.checkin(state, 0, completion, now=T0)

        self.assertFalse(outcome["monitor"]["triggered"])
        self.assertFalse(outcome["replanned"])
        self.assertEqual(len(state["plan_versions"]), 1)
        # 已执行日从计划中移除；done 按 90% 入账（同一任务当日可能有多个计划块）
        self.assertEqual(es.plan_blocks(es.current_plan(state), 0), [])
        planned_today = {}
        for b in day0:
            key = (b["course"], b["task"])
            planned_today[key] = planned_today.get(key, 0.0) + b["hours"]
        for (course_name, task_name), hours in planned_today.items():
            record = es.find_course(state, course_name)
            ts = record["task_state"][task_name]
            self.assertAlmostEqual(ts["done"], hours * 0.9)
            self.assertAlmostEqual(ts["remaining"], hours * 0.1, places=5)
        assert_conservation(self, state)

    def test_low_rate_triggers_replan(self):
        state = make_state()
        es.initial_plan(state, 0)
        day0 = es.plan_blocks(es.current_plan(state), 0)
        completion = {b["course"]: 0.2 for b in day0}
        outcome = es.checkin(state, 0, completion, now=T0)

        self.assertTrue(outcome["monitor"]["triggered"])
        self.assertTrue(outcome["replanned"])
        version = outcome["version"]
        self.assertEqual(version["mode"], "v2")
        self.assertEqual(version["kind"], "显著偏差重规划")
        self.assertEqual(version["start_day"], 1)
        self.assertTrue(version["conservation_ok"])
        # v2 只含 Day ≥ 1 的计划块
        self.assertTrue(all(int(d) >= 1 for d in version["schedule"]))
        # 反馈明细：当日计划 20% 完成、80% 回补
        for row in version["feedback"]:
            self.assertAlmostEqual(
                row["completed_hours"], row["planned_hours"] * 0.2)
        # 冷却计时起点已记录
        for b in day0:
            self.assertIn(b["course"], state["last_replan"])
        assert_conservation(self, state)

    def test_cooldown_suppresses_repeat_completion_trigger(self):
        state = make_state()
        es.initial_plan(state, 0)
        # Day 0：两门课都低完成率 → 双双触发，冷却计时同时开始
        es.checkin(state, 0, {"线性代数": 0.2, "大学英语": 0.2}, now=T0)
        self.assertEqual(len(state["plan_versions"]), 2)
        self.assertEqual(
            set(state["last_replan"]), {"线性代数", "大学英语"})

        day1 = es.plan_blocks(es.current_plan(state), 1)
        self.assertTrue(day1)
        one_hour_later = T0 + timedelta(hours=1)
        outcome = es.checkin(state, 1, {"线性代数": 0.2, "大学英语": 0.2},
                             now=one_hour_later)
        # 完成率类信号处于 24h 冷却期：全部被拦下，不生成新版本
        self.assertFalse(outcome["replanned"])
        self.assertEqual(len(outcome["monitor"]["suppressed"]), 2)
        self.assertEqual(len(state["plan_versions"]), 2)
        assert_conservation(self, state)

    def test_manual_request_bypasses_cooldown(self):
        state = make_state()
        es.initial_plan(state, 0)
        day0 = es.plan_blocks(es.current_plan(state), 0)
        es.checkin(state, 0, {b["course"]: 0.2 for b in day0}, now=T0)

        day1 = es.plan_blocks(es.current_plan(state), 1)
        outcome = es.checkin(
            state, 1, {b["course"]: 1.0 for b in day1},
            manual=True, now=T0 + timedelta(hours=1),
        )
        self.assertTrue(outcome["replanned"])
        self.assertEqual(outcome["version"]["kind"], "手动重规划")
        self.assertEqual(len(state["plan_versions"]), 3)
        assert_conservation(self, state)

    def test_day_before_exam_remains_schedulable_after_replan(self):
        """坐标系契约（状态层）：Day 0 反馈触发重规划后，
        考前一天（绝对 Day = exam_day - 1）必须仍可排课。"""
        state = es.default_state()
        state["origin_date"] = "2026-11-25"
        state["daily_hours"] = {"0": 2.0, "1": 2.0}
        state["courses"] = [
            make_course("c1", "信号与系统", 2, [
                {"name": "傅里叶变换专题", "kp": "KP", "hours": 4.0,
                 "required": True, "deps": []},
            ]),
        ]
        es.initial_plan(state, 0)
        outcome = es.checkin(state, 0, {"信号与系统": 0.0}, now=T0)
        self.assertTrue(outcome["replanned"])
        version = outcome["version"]
        day1_hours = sum(b["hours"] for b in version["schedule"].get("1", []))
        self.assertAlmostEqual(day1_hours, 2.0)  # 考前一天排满
        # D 因子按剩余天数：重规划起点 Day 1，距考试 1 天 → D = 0.9
        self.assertAlmostEqual(version["risks"]["信号与系统"]["D"], 0.9)
        assert_conservation(self, state)


class TestCourseMaintenance(unittest.TestCase):
    def test_remove_course_cleans_plan_blocks(self):
        state = make_state()
        es.initial_plan(state, 0)
        es.remove_course(state, "线性代数")
        plan = es.current_plan(state)
        for blocks in (plan.get("schedule") or {}).values():
            self.assertTrue(all(b["course"] != "线性代数" for b in blocks))
        # 删除后重排不崩溃且守恒
        version = es.replan(state, 0, kind="手动重规划")
        self.assertTrue(version["conservation_ok"])
        assert_conservation(self, state)

    def test_replan_rolls_back_future_blocks(self):
        state = make_state()
        es.initial_plan(state, 0)
        version = es.replan(state, 0, kind="约束变化重规划")
        self.assertEqual(version["start_day"], 0)
        assert_conservation(self, state)


if __name__ == "__main__":
    unittest.main()
