# -*- coding: utf-8 -*-
"""
ExamOS 核心算法模块（初赛设计书第 5 章的可运行实现）
- 5.1 课程风险评分：D / M / W / U -> R
- 5.2 时间分配优化：边际收益贪心（0.5h 时间块）
- 5.3 时间不足降级：必修/可选分级、最低复习保障、预警、剩余风险报告
- 6.3 动态重规划：执行反馈后按新状态重新求解（M 只由测验证据更新，此处演示不变）
仅依赖标准库，可直接 python scheduler.py 运行演示。
"""
import math
from dataclasses import dataclass, field

BLOCK = 0.5  # 最小时间块（小时）
H = 10       # 规划窗口（天）

# ---------------- 数据结构 ----------------

@dataclass
class Task:
    name: str
    kp: str            # 所属知识点
    hours: float       # 总工作量
    required: bool     # 必修 / 可选
    deps: list = field(default_factory=list)  # 前置任务名
    remaining: float = None
    done: float = 0.0  # 已完成时长（仅由执行反馈更新）
    def __post_init__(self):
        if self.remaining is None:
            self.remaining = self.hours

@dataclass
class Course:
    name: str
    exam_day: int          # 距考试天数 d_i（考试日不可再安排复习）
    credits: float
    degree: bool           # 是否学位课
    priority: float        # 学生自标优先级 0.33/0.67/1.0
    n_diag: int            # 有效诊断题目数（答对+答错）
    a: float               # 收益上限（与 M 解耦，由课程性质估计）
    b: float               # 学习效率
    min_hours: float       # 最低复习保障
    knowledge: list        # [(知识点, 权重 w, 掌握度 m)]
    tasks: list

# ---------------- 5.1 风险评分 ----------------

def urgency(d):            # D_i = 1 - min(d/H, 1)
    return 1.0 - min(d / H, 1.0)

def mastery_gap(course):   # M_i = 1 - Σwm / Σw
    sw = sum(w for _, w, _ in course.knowledge)
    swm = sum(w * m for _, w, m in course.knowledge)
    return 1.0 - swm / sw if sw else 1.0

def importance(course, max_credits, lam=(1/3, 1/3, 1/3)):  # W_i
    C = course.credits / max_credits
    Q = 1.0 if course.degree else 0.5
    P = course.priority
    return lam[0]*C + lam[1]*Q + lam[2]*P

def uncertainty(n):        # U_i = 1/sqrt(1+n)
    return 1.0 / math.sqrt(1 + n)

def risk_scores(courses, weights=(0.25, 0.25, 0.25, 0.25)):
    max_cr = max(c.credits for c in courses)
    report = {}
    for c in courses:
        D = urgency(c.exam_day)
        M = mastery_gap(c)
        W = importance(c, max_cr)
        U = uncertainty(c.n_diag)
        R = weights[0]*D + weights[1]*M + weights[2]*W + weights[3]*U
        report[c.name] = dict(D=round(D,3), M=round(M,3), W=round(W,3),
                              U=round(U,3), R=round(R,3))
    return report

# ---------------- 5.2 收益函数与贪心分配 ----------------

def gain(a, b, x):         # G_i(x) = a(1 - e^{-bx})
    return a * (1.0 - math.exp(-b * x))

def marginal(course, R, x):  # R_i * [G_i(x+0.5) - G_i(x)]
    return R * (gain(course.a, course.b, x + BLOCK) - gain(course.a, course.b, x))

def deps_done(task, course):
    done = {t.name for t in course.tasks if t.remaining <= 1e-9}
    return all(d in done for d in task.deps)

def pick(courses, risks, allocated, day):
    """在当日可行的任务中选边际收益最大的 (course, task)。
    必修优先（5.3）：存在可行必修任务时不分配可选任务；
    只有必修全部排完（或不可行）才考虑可选任务。"""
    for required_first in (True, False):
        best, best_gain = None, 0.0
        for c in courses:
            if day >= c.exam_day:      # 考试日及之后不可再复习该课
                continue
            for t in c.tasks:
                if t.required != required_first:
                    continue
                if t.remaining > 1e-9 and deps_done(t, c):
                    g = marginal(c, risks[c.name]["R"], allocated[c.name])
                    if g > best_gain:
                        best, best_gain = (c, t), g
        if best:
            return best
    return None

def allocate(courses, daily_hours, risks, start_day=0):
    """返回 (schedule, allocated, warnings, dropped)。"""
    allocated = {c.name: 0.0 for c in courses}
    schedule = {}
    warnings, dropped = [], []
    last_day = max(c.exam_day for c in courses) - 1
    days = [d for d in range(start_day, last_day + 1)]

    # --- 时间不足预警 ---
    capacity = sum(daily_hours.get(d, 0) for d in days)
    req_total = sum(t.remaining for c in courses for t in c.tasks if t.required)
    if req_total > capacity:
        warnings.append(
            f"时间不足预警：必修任务共需 {req_total:.1f}h，"
            f"可用时间仅 {capacity:.1f}h，缺口 {req_total-capacity:.1f}h。"
            f"将按最低损失原则裁剪任务。")

    # --- 最低复习保障：先为每门课排入 min_hours（必修任务优先） ---
    for c in courses:
        while allocated[c.name] < c.min_hours - 1e-9:
            req = [t for t in c.tasks if t.required and t.remaining > 1e-9 and deps_done(t, c)]
            opt = [t for t in c.tasks if not t.required and t.remaining > 1e-9 and deps_done(t, c)]
            cand = req or opt
            if not cand:
                break
            t = max(cand, key=lambda t: marginal(c, risks[c.name]["R"], allocated[c.name]))
            day = next((d for d in days if d < c.exam_day
                        and sum(b[2] for b in schedule.get(d, [])) < daily_hours.get(d, 0)), None)
            if day is None:
                warnings.append(f"最低保障不足：{c.name} 最低复习保障 "
                                f"{c.min_hours}h 未能排满（已排 {allocated[c.name]:.1f}h）。")
                break
            day_left = daily_hours.get(day, 0) - sum(b[2] for b in schedule.get(day, []))
            chunk = min(BLOCK, t.remaining, day_left,
                         c.min_hours - allocated[c.name])  # 不超过本课程最低保障目标
            t.remaining -= chunk
            allocated[c.name] += chunk
            schedule.setdefault(day, []).append((c.name, t.name, chunk))

    # --- 主循环：按日推进，每块选边际收益最大任务 ---
    for day in days:
        budget = daily_hours.get(day, 0) - sum(b[2] for b in schedule.get(day, []))
        while budget > 1e-9:
            got = pick(courses, risks, allocated, day)
            if not got:
                break
            c, t = got
            chunk = min(BLOCK, t.remaining, budget)     # 粒度截断：尾数不足 0.5h 按实际计
            t.remaining -= chunk
            allocated[c.name] += chunk
            schedule.setdefault(day, []).append((c.name, t.name, chunk))
            budget -= chunk

    # --- 剩余风险报告：未排入的任务 ---
    for c in courses:
        for t in c.tasks:
            if t.remaining > 1e-9:
                kind = "必修" if t.required else "可选"
                if t.required:
                    dropped.append(t)
                warnings.append(
                    f"[{c.name}] {kind}任务未排入：{t.name}（{t.kp}），"
                    f"剩余 {t.remaining:.1f}h，构成失分风险敞口。")
    return schedule, allocated, warnings, dropped

# ---------------- 执行反馈与计划退回（6.3） ----------------

def apply_day_completion(courses, schedule, day, completion=None, default_rate=1.0):
    """按任务聚合某日执行：计划 × 完成率，未完成部分回补剩余工作量。
    完成时长记入 task.done；返回明细 [(课程, 任务, 计划h, 完成h, 回补h)]。"""
    completion = completion or {}
    planned = {}
    for cname, tname, h in schedule.get(day, []):
        planned[(cname, tname)] = planned.get((cname, tname), 0) + h
    detail = []
    for (cname, tname), p in sorted(planned.items()):
        course = next(c for c in courses if c.name == cname)
        task = next(t for t in course.tasks if t.name == tname)
        rate = float(completion.get(cname, default_rate))
        if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
            raise ValueError(f"{cname} 完成率必须在 0 到 1 之间，实际为 {rate!r}")
        done = p * rate  # 执行完成率可为任意比例，不再强制取整到 0.5h
        task.done += done
        task.remaining += p - done       # 未完成部分回补剩余工作量
        detail.append((cname, tname, p, done, p - done))
    schedule.pop(day, None)              # 已执行日转为历史，移出当前计划
    return detail

def rollback_future(courses, schedule, start_day):
    """废弃 start_day 及之后未执行的计划块，退回任务剩余工作量；
    同时从 schedule 移除这些日期的计划。返回 {day: 退回小时数}。"""
    back = {}
    for d in sorted(list(schedule.keys())):
        if d < start_day:
            continue
        for cname, tname, h in schedule[d]:
            course = next(c for c in courses if c.name == cname)
            task = next(t for t in course.tasks if t.name == tname)
            task.remaining += h
            back[d] = back.get(d, 0) + h
        del schedule[d]
    return back

def conservation_check(courses, schedule):
    """守恒校验：任务总量 = 已完成 + 当前计划分配 + 剩余未安排。"""
    planned = {}
    for blocks in schedule.values():
        for cname, tname, h in blocks:
            planned[(cname, tname)] = planned.get((cname, tname), 0) + h
    bad = []
    for c in courses:
        for t in c.tasks:
            p = planned.get((c.name, t.name), 0)
            if abs(t.hours - (t.done + p + t.remaining)) > 1e-6:
                bad.append((c.name, t.name))
    return bad

def hours_by_day(schedule):
    """{day: {course: hours}}，用于 v1/v2 差异对比。"""
    out = {}
    for d, blocks in schedule.items():
        for cname, _, h in blocks:
            out.setdefault(d, {}).setdefault(cname, 0.0)
            out[d][cname] += h
    return out

def show_plan_diff(v1_days, v2_days):
    """打印 v1/v2 按日按课程的分配差异报告。"""
    days = sorted(set(v1_days) | set(v2_days))
    names = sorted({n for d in days for n in set(v1_days.get(d, {})) | set(v2_days.get(d, {}))})
    print(f"\n{'日/课程':<16}{'v1':>8}{'v2':>8}{'Δ':>8}")
    for d in days:
        for n in names:
            a = v1_days.get(d, {}).get(n, 0.0)
            b = v2_days.get(d, {}).get(n, 0.0)
            if abs(a - b) > 1e-9:
                print(f"Day {d} {n:<10}{a:>7.1f}h{b:>7.1f}h{b-a:>+7.1f}h")

# ---------------- 演示场景 ----------------

def build_courses():
    """模拟考试周：距今天 1/3/5 天三门课（对应设计书 5.1 示例）。"""
    math_tasks = [
        Task("极限与连续错题重练", "极限与连续", 2.0, True),
        Task("偏导数专题", "偏导数", 2.5, True),
        Task("多元微分综合题", "多元微分", 2.5, True, deps=["偏导数专题"]),
        Task("历年卷模拟一", "综合", 3.0, True, deps=["多元微分综合题"]),
        Task("错题本二刷", "综合", 2.0, False),
    ]
    la_tasks = [
        Task("矩阵运算强化", "矩阵运算", 2.0, True),
        Task("特征值与特征向量", "特征值", 3.0, True, deps=["矩阵运算强化"]),
        Task("二次型讲义精读", "二次型", 2.0, False),
    ]
    en_tasks = [
        Task("核心词汇过一遍", "词汇", 2.0, True),
        Task("阅读真题两套", "阅读", 2.5, True),
        Task("作文模板整理", "写作", 1.5, False),
    ]
    return [
        Course("高等数学", exam_day=1, credits=6, degree=True, priority=1.0,
               n_diag=20, a=0.85, b=0.18, min_hours=3.0,
               knowledge=[("极限与连续", 0.3, 0.6), ("偏导数", 0.3, 0.4),
                          ("多元微分", 0.4, 0.3)],
               tasks=math_tasks),
        Course("线性代数", exam_day=3, credits=4, degree=True, priority=0.67,
               n_diag=10, a=0.80, b=0.20, min_hours=2.0,
               knowledge=[("矩阵运算", 0.4, 0.7), ("特征值", 0.6, 0.5)],
               tasks=la_tasks),
        Course("大学英语", exam_day=5, credits=3, degree=False, priority=0.33,
               n_diag=45, a=0.60, b=0.15, min_hours=1.5,
               knowledge=[("词汇", 0.5, 0.8), ("阅读", 0.5, 0.7)],
               tasks=en_tasks),
    ]

def show_plan(title, courses, schedule, allocated, warnings):
    """任务聚合视图：每个任务一行标题，下列各日分配；附总量/已完成/剩余统计。"""
    print(f"\n{'='*58}\n{title}\n{'='*58}")
    daily_total = {d: sum(b[2] for b in blocks) for d, blocks in schedule.items()}
    print("每日概览：" + "，".join(f"Day {d} 共 {h:.1f}h"
                                 for d, h in sorted(daily_total.items())))
    for c in courses:
        print(f"\n—— {c.name} ——")
        for t in c.tasks:
            planned = {}
            for d, blocks in schedule.items():
                for cname, tname, h in blocks:
                    if cname == c.name and tname == t.name:
                        planned[d] = planned.get(d, 0) + h
            total_planned = sum(planned.values())
            kind = "必修" if t.required else "可选"
            print(f"【{t.name}】{kind}（{t.kp}）")
            for d in sorted(planned):
                print(f"  Day {d}      {planned[d]:.1f} h")
            print(f"  任务总量 {t.hours:.1f} h | 已完成 {t.done:.1f} h | "
                  f"本计划分配 {total_planned:.1f} h | 未安排 {t.remaining:.1f} h")
    print("\n各课程分配合计：" + "，".join(f"{k} {v:.1f}h" for k, v in allocated.items()))
    print("\n--- 预警与剩余风险报告 ---")
    for w in warnings:
        print(" *", w)
    if not warnings:
        print(" （无：全部任务已排入）")

def main():
    # ---------- v1：初始规划 ----------
    courses = build_courses()
    risks = risk_scores(courses)
    print("=" * 58)
    print("一、课程风险评分（5.1，H=10）")
    print("=" * 58)
    print(f"{'课程':<6}{'D':>7}{'M':>7}{'W':>7}{'U':>7}{'R':>7}")
    for name, r in risks.items():
        print(f"{name:<6}{r['D']:>7}{r['M']:>7}{r['W']:>7}{r['U']:>7}{r['R']:>7}")

    daily = {d: 6.0 for d in range(0, 5)}   # 每天 6h 复习时间（制造轻度时间不足）
    sched1, alloc1, warn1, _ = allocate(courses, daily, risks)
    v1_days = hours_by_day(sched1)          # v1 快照（退回前），供 v1/v2 差异报告
    show_plan("二、初始复习计划 v1（贪心边际收益分配）", courses, sched1, alloc1, warn1)

    # ---------- 模拟 Day 0 执行：线代完成率仅 25% ----------
    print("\n" + "=" * 58)
    print("三、Day 0 执行反馈（模拟：线代当日任务完成率 25%）")
    print("=" * 58)
    for cname, tname, p, done, back in apply_day_completion(
            courses, sched1, 0, completion={"线性代数": 0.25}):
        print(f"  {cname}《{tname}》计划 {p:.1f}h，完成 {done:.1f}h，"
              f"回补剩余工作量 {back:.1f}h")

    # v1 中 Day>=1 的计划块被废弃，退回任务池
    print("\n  废弃 v1 中 Day≥1 的未执行计划块，退回剩余工作量：")
    for d, h in rollback_future(courses, sched1, start_day=1).items():
        print(f"  Day {d} 计划已退回（{h:.1f}h）")
    bad = conservation_check(courses, sched1)
    print(f"  守恒校验（总量=已完成+已分配+未安排）：{'通过' if not bad else '失败 ' + str(bad)}")
    print("  注：M_i 不变（无新测验证据）；D_i 随考试临近自然上升；"
          "剩余工作量按完成记录更新。")

    # ---------- v2：动态重规划 ----------
    for c in courses:
        c.exam_day -= 1   # 时间流逝一天
    risks2 = risk_scores(courses)
    print("\n重规划后风险评分变化（D_i 上升，M_i 不变）：")
    for name in risks2:
        print(f"  {name}: R {risks[name]['R']} -> {risks2[name]['R']}，"
              f"M {risks[name]['M']} -> {risks2[name]['M']}")
    sched2, alloc2, warn2, _ = allocate(courses, daily, risks2, start_day=1)
    bad2 = conservation_check(courses, sched2)
    show_plan("四、动态重规划 v2（更新剩余工作量与紧迫度后重新求解）",
              courses, sched2, alloc2, warn2)
    print(f"\n守恒校验：{'通过' if not bad2 else '失败 ' + str(bad2)}")
    print("\n五、v1 / v2 分配差异报告（按日按课程，Day 0 已执行不参与对比）")
    show_plan_diff({d: v for d, v in v1_days.items() if d >= 1}, hours_by_day(sched2))

if __name__ == "__main__":
    main()