# -*- coding: utf-8 -*-
"""Community Intelligence（内核）：社区行为 → 统一证据库。

本层只做确定性的证据转换与门禁，不做内容理解；
帖子知识点识别与回答质量评分由上游（LLM 分析 Agent 或人工）
产出后，以 CommunityEvidence 形式进入本层。
"""
