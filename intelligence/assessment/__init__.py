# -*- coding: utf-8 -*-
"""Assessment Intelligence：能力验证与证据校准（Competency Verification）。

设计原则（来自 Evidence-Calibrated Student Modeling 讨论）：
- 系统的目标不是识别和惩罚 AI 使用，而是避免把 AI 生成的答案
  错误地当成学生自己的能力证据；
- 所有证据权重集中在本模块的 evidence_policy.py，标注为
  「默认参数，需真实数据校准」，禁止散落到各引擎中写死；
- LLM 不参与证据融合的最终计算。
"""
