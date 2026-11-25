# -*- coding: utf-8 -*-
"""五页面冒烟测试：用 streamlit AppTest 无头运行每个页面，捕获渲染异常。

运行：.venv/Scripts/python smoke_pages.py
注意：会使用并清理临时状态文件，不触碰真实 examos_data.json。
"""
import os
import sys
import tempfile

os.chdir(os.path.dirname(os.path.abspath(__file__)))
# 状态文件重定向到临时目录，避免污染真实数据
# （模块属性级补丁：pages 经 import examos_state 调用，运行时查表生效）
_tmp = tempfile.mkdtemp(prefix="examos-smoke-")
import examos_state as es

_tmp_state = os.path.join(_tmp, "state.json")
_load_state = es.load_state
_save_state = es.save_state
es.load_state = lambda path=_tmp_state: _load_state(path)
es.save_state = lambda state, path=_tmp_state: _save_state(state, path)

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = [
    "examos_app.py",
    "pages/1_课程与考试.py",
    "pages/2_复习计划.py",
    "pages/3_执行反馈.py",
    "pages/4_诊断与画像.py",
]

failed = False
for page in PAGES:
    at = AppTest.from_file(page, default_timeout=30)
    at.run()
    if at.exception:
        failed = True
        print(f"[FAIL] {page}")
        for exc in at.exception:
            print("   ", exc.value if hasattr(exc, "value") else exc)
    else:
        print(f"[ OK ] {page}")

# 含数据场景：建档 → 生成计划 → 打卡 → 再跑一遍全部页面
import examos_state as state_mod  # noqa: E402

state = state_mod.load_state()
state["daily_hours"] = {str(d): 4.0 for d in range(6)}
state["courses"] = [{
    "course_id": "c1", "name": "线性代数", "exam_day": 4,
    "credits": 4.0, "degree": True, "priority": 0.67, "n_diag": 10,
    "a": 0.8, "b": 0.2, "min_hours": 0.0,
    "knowledge": [{"name": "矩阵运算", "weight": 1.0, "mastery": 0.5,
                   "mastery_source": "中性先验"}],
    "tasks": [
        {"name": "矩阵运算强化", "kp": "矩阵运算", "hours": 2.0,
         "required": True, "deps": []},
    ],
    "task_state": {},
}]
state_mod.save_state(state)
state_mod.initial_plan(state, 0)
state_mod.checkin(state, 0, {"线性代数": 0.2})
state_mod.save_state(state)

for page in PAGES:
    at = AppTest.from_file(page, default_timeout=30)
    at.run()
    if at.exception:
        failed = True
        print(f"[FAIL] {page}（含数据）")
        for exc in at.exception:
            print("   ", exc.value if hasattr(exc, "value") else exc)
    else:
        print(f"[ OK ] {page}（含数据）")

print("SMOKE", "FAILED" if failed else "PASSED")
sys.exit(1 if failed else 0)
