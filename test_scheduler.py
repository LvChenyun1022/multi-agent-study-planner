# -*- coding: utf-8 -*-
"""scheduler.py 边界测试（标准库 unittest，直接 python -m unittest test_scheduler -v 运行）"""
import math
import unittest

from scheduler import (BLOCK, H, Course, Task, urgency, mastery_gap, importance,
                       uncertainty, risk_scores, gain, allocate,
                       apply_day_completion, rollback_future, conservation_check,
                       build_courses)


def one_course(**kw):
    base = dict(name="测试课", exam_day=3, credits=4, degree=True, priority=1.0,
                n_diag=10, a=0.8, b=0.2, min_hours=0.0,
                knowledge=[("KP1", 1.0, 0.5)], tasks=[Task("T1", "KP1", 2.0, True)])
    base.update(kw)
    return Course(**base)


class TestRiskFactors(unittest.TestCase):
    def test_urgency_boundary(self):
        self.assertAlmostEqual(urgency(1), 0.9)          # 考前 1 天
        self.assertAlmostEqual(urgency(0), 1.0)          # 考试当天
        self.assertAlmostEqual(urgency(H), 0.0)          # 窗口边缘
        self.assertAlmostEqual(urgency(H + 30), 0.0)     # 窗口外恒为 0

    def test_mastery_gap_weighted(self):
        c = one_course(knowledge=[("A", 1.0, 1.0), ("B", 3.0, 0.0)])
        self.assertAlmostEqual(mastery_gap(c), 0.75)     # 1 - 1/4
        c2 = one_course(knowledge=[("A", 1.0, 1.0)])
        self.assertAlmostEqual(mastery_gap(c2), 0.0)     # 全掌握
        c3 = one_course(knowledge=[])
        self.assertAlmostEqual(mastery_gap(c3), 1.0)     # 无数据视为全缺口

    def test_uncertainty_monotonic(self):
        self.assertAlmostEqual(uncertainty(0), 1.0)      # 无证据最不确定
        self.assertGreater(uncertainty(1), uncertainty(100))

    def test_importance_weights(self):
        c = one_course(credits=4, degree=True, priority=1.0)
        # C=1, Q=1, P=1，等权 -> W=1
        self.assertAlmostEqual(importance(c, max_credits=4), 1.0)

    def test_gain_monotonic_and_bounded(self):
        self.assertAlmostEqual(gain(0.8, 0.2, 0), 0.0)
        self.assertLess(gain(0.8, 0.2, 5), 0.8)          # 不超过上限 a
        self.assertGreater(gain(0.8, 0.2, 5), gain(0.8, 0.2, 2))  # 单调递增
        # 边际收益递减
        m1 = gain(0.8, 0.2, 0.5) - gain(0.8, 0.2, 0)
        m2 = gain(0.8, 0.2, 5.5) - gain(0.8, 0.2, 5.0)
        self.assertGreater(m1, m2)


class TestAllocationConstraints(unittest.TestCase):
    def test_deadline_no_study_on_or_after_exam(self):
        c = one_course(exam_day=1, tasks=[Task("T1", "KP1", 4.0, True)])
        daily = {0: 8.0, 1: 8.0, 2: 8.0}
        risks = risk_scores([c])
        sched, alloc, warns, dropped = allocate([c], daily, risks)
        self.assertNotIn(1, sched)          # 考试日当天不排
        self.assertNotIn(2, sched)
        self.assertEqual(alloc["测试课"], 4.0)
        self.assertEqual(sum(b[2] for b in sched[0]), 4.0)

    def test_dependency_order(self):
        # 后置任务必须等前置任务全部排完才出现
        c = one_course(tasks=[Task("前置", "KP1", 1.0, True),
                              Task("后置", "KP1", 1.0, True, deps=["前置"])])
        daily = {0: 8.0, 1: 8.0, 2: 8.0}
        risks = risk_scores([c])
        sched, _, _, _ = allocate([c], daily, risks)
        first = sched[0][0][1]
        self.assertEqual(first, "前置")
        # 前置排完之前，后置不能被排入（在同一天内也保持顺序）
        names = [b[1] for b in sched[0]]
        self.assertLess(names.index("前置"), names.index("后置"))

    def test_shortage_no_crash_and_residual_risk(self):
        # 必修 10h，可用仅 2h：不报无解，输出计划 + 剩余风险
        c = one_course(tasks=[Task("T1", "KP1", 10.0, True)])
        daily = {0: 2.0}
        risks = risk_scores([c])
        sched, alloc, warns, dropped = allocate([c], daily, risks)
        self.assertTrue(any("时间不足预警" in w for w in warns))
        self.assertTrue(any("未排入" in w for w in warns))
        self.assertEqual(len(dropped), 1)
        self.assertEqual(alloc["测试课"], 2.0)   # 时间全部用满

    def test_required_beats_optional(self):
        # 可选任务排在列表第一位、必修第二位，仅 1h 预算：必修必须先排
        c = one_course(tasks=[Task("可选任务", "KP1", 1.0, False),
                              Task("必修任务", "KP1", 1.0, True)])
        daily = {0: 1.0}   # 总预算只有 1 小时：可选不得抢占必修
        risks = risk_scores([c])
        sched, _, _, _ = allocate([c], daily, risks)
        day0 = sched.get(0, [])
        self.assertEqual(day0[0][1], "必修任务")
        opt = next(t for t in c.tasks if t.name == "可选任务")
        req = next(t for t in c.tasks if t.name == "必修任务")
        self.assertAlmostEqual(req.remaining, 0.0)
        self.assertAlmostEqual(opt.remaining, 1.0)

    def test_sub_block_task_no_negative_remaining(self):
        # 0.25h 任务：按实际尾数分配，剩余不得为负
        c = one_course(tasks=[Task("小任务", "KP1", 0.25, True)])
        daily = {0: 2.0, 1: 2.0, 2: 2.0}
        risks = risk_scores([c])
        sched, alloc, _, _ = allocate([c], daily, risks)
        t = c.tasks[0]
        self.assertAlmostEqual(alloc["测试课"], 0.25)
        self.assertGreaterEqual(t.remaining, 0.0)
        self.assertAlmostEqual(t.remaining, 0.0)

    def test_never_exceed_daily_budget(self):
        c = one_course(tasks=[Task("T1", "KP1", 6.0, True)])
        daily = {0: 1.75, 1: 0.5, 2: 3.0}   # 非 0.5 整数倍的日预算
        risks = risk_scores([c])
        sched, _, _, _ = allocate([c], daily, risks)
        for d, blocks in sched.items():
            self.assertLessEqual(sum(b[2] for b in blocks), daily[d] + 1e-9)

    def test_min_guarantee_priority(self):
        # 低风险课程也应先获得最低保障
        c1 = one_course(name="高险课", min_hours=0.0,
                        tasks=[Task("A", "KP1", 8.0, True)])
        c2 = one_course(name="低险课", exam_day=3, min_hours=2.0,
                        n_diag=100, priority=0.33, degree=False,
                        knowledge=[("KP1", 1.0, 0.95)],
                        tasks=[Task("B", "KP1", 3.0, False)])
        daily = {0: 4.0, 1: 4.0, 2: 4.0}
        risks = risk_scores([c1, c2])
        sched, alloc, _, _ = allocate([c1, c2], daily, risks)
        self.assertGreaterEqual(alloc["低险课"], 2.0 - 1e-9)


    def test_minimum_guarantee_does_not_overshoot_or_starve_next_course(self):
        # 第一门课的最低保障为 0.6h；不能因 0.5h 粒度多拿 0.15h，
        # 否则第二门课的 0.6h 最低保障会被挤占。
        c1 = one_course(name="课程A", min_hours=0.6,
                        tasks=[Task("A任务", "KP1", 0.75, True)])
        c2 = one_course(name="课程B", min_hours=0.6,
                        tasks=[Task("B任务", "KP1", 0.6, True)])
        daily = {0: 1.2, 1: 0.0, 2: 0.0}
        risks = risk_scores([c1, c2])
        _, allocated, _, _ = allocate([c1, c2], daily, risks)
        self.assertAlmostEqual(allocated["课程A"], 0.6)
        self.assertAlmostEqual(allocated["课程B"], 0.6)

    def test_sub_block_completion_is_not_rounded_away(self):
        # 0.25h 任务即使 100% 完成，也必须记录 0.25h，而不是被 0.5h 四舍五入吞掉。
        c = one_course(tasks=[Task("小任务", "KP1", 0.25, True)])
        daily = {0: 0.25, 1: 0.0, 2: 0.0}
        risks = risk_scores([c])
        sched, _, _, _ = allocate([c], daily, risks)
        detail = apply_day_completion([c], sched, 0, default_rate=1.0)
        self.assertAlmostEqual(detail[0][3], 0.25)
        self.assertAlmostEqual(c.tasks[0].done, 0.25)
        self.assertAlmostEqual(c.tasks[0].remaining, 0.0)
        self.assertEqual(conservation_check([c], sched), [])

    def test_completion_rate_out_of_range_is_rejected(self):
        c = one_course(tasks=[Task("T1", "KP1", 0.5, True)])
        daily = {0: 0.5, 1: 0.0, 2: 0.0}
        sched, _, _, _ = allocate([c], daily, risk_scores([c]))
        with self.assertRaises(ValueError):
            apply_day_completion([c], sched, 0, completion={"测试课": 1.1})


class TestReplanning(unittest.TestCase):
    def test_completion_rollback_conservation(self):
        """四项验收标准之三、之四：回补 + 退回 + 守恒。"""
        courses = build_courses()
        risks = risk_scores(courses)
        daily = {d: 6.0 for d in range(0, 5)}
        sched, _, _, _ = allocate(courses, daily, risks)

        # Day 0 执行：线代完成 25%
        apply_day_completion(courses, sched, 0, completion={"线性代数": 0.25})
        la = next(c for c in courses if c.name == "线性代数")
        t = next(t for t in la.tasks if t.name == "矩阵运算强化")
        self.assertAlmostEqual(t.done, 0.5)          # 计划 2h × 25%
        self.assertAlmostEqual(t.remaining, 1.5)     # 回补 1.5h

        # 退回未执行计划块后必须守恒（任务不凭空消失/增殖）
        rollback_future(courses, sched, start_day=1)
        self.assertEqual(conservation_check(courses, sched), [])

        # 时间流逝 + 重规划后仍然守恒
        for c in courses:
            c.exam_day -= 1
        risks2 = risk_scores(courses)
        sched2, _, _, _ = allocate(courses, daily, risks2, start_day=1)
        self.assertEqual(conservation_check(courses, sched2), [])

    def test_mastery_unchanged_without_new_evidence(self):
        """没有新测验证据时，重规划不得修改 M_i。"""
        courses = build_courses()
        m_before = {c.name: mastery_gap(c) for c in courses}
        risks = risk_scores(courses)
        daily = {d: 6.0 for d in range(0, 5)}
        sched, _, _, _ = allocate(courses, daily, risks)
        apply_day_completion(courses, sched, 0, completion={"线性代数": 0.25})
        rollback_future(courses, sched, start_day=1)
        for c in courses:
            c.exam_day -= 1
        risks2 = risk_scores(courses)
        for name in m_before:
            self.assertAlmostEqual(risks[name]["M"], risks2[name]["M"])

    def test_replan_shifts_time_to_lagging_course(self):
        """完成率低的课程在 v2 中应获得更多边际时间（D 上升 + 剩余工作量增加）。"""
        courses = build_courses()
        risks = risk_scores(courses)
        daily = {d: 6.0 for d in range(0, 5)}
        sched1, _, _, _ = allocate(courses, daily, risks)
        apply_day_completion(courses, sched1, 0, completion={"线性代数": 0.25})
        rollback_future(courses, sched1, start_day=1)
        for c in courses:
            c.exam_day -= 1
        risks2 = risk_scores(courses)
        self.assertGreater(risks2["线性代数"]["R"], risks["线性代数"]["R"])  # D 上升
        sched2, _, _, _ = allocate(courses, daily, risks2, start_day=1)
        day1_la = sum(h for cn, tn, h in sched2.get(1, []) if cn == "线性代数")
        self.assertGreaterEqual(day1_la, 3.0)   # 线代在 Day 1 获得明显倾斜


if __name__ == "__main__":
    unittest.main()