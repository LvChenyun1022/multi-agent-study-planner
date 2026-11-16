# -*- coding: utf-8 -*-
"""ExamOS 2.0 数据模型层。

与 1.0 的兼容契约：
- 不修改 scheduler.py 的公开算法接口；
- 不修改 agents/scheduler_adapter.py 的 run_exam_plan() 输入格式；
- 新数据结构一律经过本层验证后才进入服务层与决策层。

实现说明：为与仓库既有风格（scheduler_adapter.py 的手工校验）保持一致，
本层使用标准库 dataclass + 显式校验，不引入 pydantic 等新依赖，
保证 CI 与部署环境零新增安装项。
"""
