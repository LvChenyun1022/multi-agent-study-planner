# -*- coding: utf-8 -*-
"""决赛演示种子数据：基于吕忱耘 2026-2027-1 真实课表构造。

场景：2026-12-25（周五，第 16 周末）进入考试周备战——
- 中级微观经济学（4 学分，钟根元）12/28 考试，倒计时 3 天；
- 离散数学（3 学分，赖奕甫）12/30 考试，倒计时 5 天；
- 中级宏观经济学（3 学分，喻洋）12/31 考试，倒计时 6 天；
- 商务统计学（2 学分，黄雯馨）2027-01-04 考试，倒计时 10 天。

时间预算按真实课表推算：Day 0（周五）白天尚有
室内高尔夫（3-4 节）与习概（6-8 节），仅晚间约 3h 可复习；
Day 1 起进入考试周无课，每天 8h。

掌握度为【自评/中性先验】演示值，未经真实测验证据校准；
考试日期为演示设定（原型阶段系统不接入教务系统）。

用法：python scripts/seed_demo.py
效果：在项目根目录生成 examos_data.json（已被 .gitignore 忽略），
随后 streamlit run examos_app.py 即可按《演示彩排脚本》演练。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import examos_state as es  # noqa: E402

ORIGIN = "2026-12-25"  # Day 0：周五（第 16 周最后一天有课）

COURSES = [
    {
        "course_id": "ECON2404-02",
        "name": "中级微观经济学",
        "exam_day": 3,          # 2026-12-28 周一
        "credits": 4.0, "degree": True, "priority": 1.0, "n_diag": 20,
        "a": 0.85, "b": 0.18, "min_hours": 3.0,
        "knowledge": [
            {"name": "消费者理论", "weight": 0.30, "mastery": 0.60,
             "mastery_source": "自评"},
            {"name": "生产者理论", "weight": 0.25, "mastery": 0.50,
             "mastery_source": "自评"},
            {"name": "一般均衡与福利经济学", "weight": 0.25, "mastery": 0.40,
             "mastery_source": "自评"},
            {"name": "博弈论基础", "weight": 0.20, "mastery": 0.35,
             "mastery_source": "自评"},
        ],
        "tasks": [
            {"name": "消费者理论错题重练", "kp": "消费者理论",
             "hours": 2.0, "required": True, "deps": []},
            {"name": "生产者理论专题", "kp": "生产者理论",
             "hours": 2.5, "required": True, "deps": []},
            {"name": "一般均衡与福利定理攻坚", "kp": "一般均衡与福利经济学",
             "hours": 2.5, "required": True, "deps": ["生产者理论专题"]},
            {"name": "历年卷模拟一（限时）", "kp": "一般均衡与福利经济学",
             "hours": 3.0, "required": True,
             "deps": ["一般均衡与福利定理攻坚"]},
            {"name": "博弈论习题选做", "kp": "博弈论基础",
             "hours": 1.5, "required": False, "deps": []},
        ],
        "task_state": {},
    },
    {
        "course_id": "CS2501-06",
        "name": "离散数学",
        "exam_day": 5,          # 2026-12-30 周三
        "credits": 3.0, "degree": True, "priority": 0.67, "n_diag": 10,
        "a": 0.80, "b": 0.20, "min_hours": 2.5,
        "knowledge": [
            {"name": "命题与谓词逻辑", "weight": 0.30, "mastery": 0.70,
             "mastery_source": "自评"},
            {"name": "图论", "weight": 0.40, "mastery": 0.45,
             "mastery_source": "自评"},
            {"name": "组合计数", "weight": 0.30, "mastery": 0.50,
             "mastery_source": "自评"},
        ],
        "tasks": [
            {"name": "逻辑推理证明题强化", "kp": "命题与谓词逻辑",
             "hours": 2.0, "required": True, "deps": []},
            {"name": "图论专题（欧拉图与哈密顿图）", "kp": "图论",
             "hours": 3.0, "required": True, "deps": ["逻辑推理证明题强化"]},
            {"name": "组合计数习题", "kp": "组合计数",
             "hours": 2.0, "required": False, "deps": []},
        ],
        "task_state": {},
    },
    {
        "course_id": "ECON2403-06",
        "name": "中级宏观经济学",
        "exam_day": 6,          # 2026-12-31 周四
        "credits": 3.0, "degree": True, "priority": 0.67, "n_diag": 8,
        "a": 0.75, "b": 0.18, "min_hours": 2.0,
        "knowledge": [
            {"name": "IS-LM 模型", "weight": 0.35, "mastery": 0.55,
             "mastery_source": "自评"},
            {"name": "AD-AS 与通胀", "weight": 0.35, "mastery": 0.40,
             "mastery_source": "自评"},
            {"name": "增长理论", "weight": 0.30, "mastery": 0.50,
             "mastery_source": "自评"},
        ],
        "tasks": [
            {"name": "IS-LM 计算题专项", "kp": "IS-LM 模型",
             "hours": 2.5, "required": True, "deps": []},
            {"name": "索洛模型推导", "kp": "增长理论",
             "hours": 2.0, "required": True, "deps": []},
            {"name": "货币政策案例分析", "kp": "AD-AS 与通胀",
             "hours": 1.5, "required": False, "deps": []},
        ],
        "task_state": {},
    },
    {
        "course_id": "BUSS2302-03",
        "name": "商务统计学",
        "exam_day": 10,         # 2027-01-04 周一
        "credits": 2.0, "degree": False, "priority": 0.33, "n_diag": 25,
        "a": 0.60, "b": 0.15, "min_hours": 1.5,
        "knowledge": [
            {"name": "假设检验", "weight": 0.50, "mastery": 0.75,
             "mastery_source": "自评"},
            {"name": "回归分析", "weight": 0.50, "mastery": 0.65,
             "mastery_source": "自评"},
        ],
        "tasks": [
            {"name": "假设检验题型过一遍", "kp": "假设检验",
             "hours": 1.5, "required": True, "deps": []},
            {"name": "回归分析上机题复盘", "kp": "回归分析",
             "hours": 1.5, "required": False, "deps": []},
        ],
        "task_state": {},
    },
]

# 每日净可用复习时间：Day 0 周五白天有课（高尔夫 3-4 节、习概 6-8 节），
# 仅晚间约 3h；Day 1 起考试周无课，每天 8h。
DAILY_HOURS = {"0": 3.0, **{str(d): 8.0 for d in range(1, 10)}}


def main() -> None:
    state = es.default_state()
    state["origin_date"] = ORIGIN
    state["daily_hours"] = dict(DAILY_HOURS)
    for record in COURSES:
        es.upsert_course(state, record)
    es.save_state(state)
    print(f"已写入 {es.STATE_PATH}")
    print(f"规划原点：{ORIGIN}（Day 0，周五）")
    for c in COURSES:
        print(f"  · {c['name']}：{c['credits']:g} 学分，"
              f"考试 Day {c['exam_day']}"
              f"（{es.day_to_date(state, c['exam_day'])}），"
              f"任务 {len(c['tasks'])} 项")
    print("每日可用时间：Day 0 为 3h（周五有课），Day 1-9 为 8h（考试周）")
    print("下一步：在「复习计划」页点击「生成初始复习计划」开始演示。")


if __name__ == "__main__":
    main()
