# -*- coding: utf-8 -*-
"""
memory.py — MemoryStore：跨会话长期记忆（课程 Memory 三层概念的落地）

课程中 Agent 的记忆通常分三层，本项目的对应关系：
1. **短期记忆（Short-term Memory）** = ContextStore（context_store.py）：
   只活在"本次会话"里——session_id、task_chain、pending_approval 等，
   每次 run_session 都会 reset 清空，会话结束即失效。
2. **长期记忆（Long-term Memory）** = 本文件 memory.json 里的 "profile"：
   跨会话稳定不变的用户画像（薄弱学科、运动安排、时间偏好等），
   下次启动新会话时直接加载，Agent 不必从头问起。
3. **情景记忆（Episodic Memory）** = memory.json 里的 "episodes"：
   每次会话结束后留下的一条摘要记录（时间、结论、反馈），
   让 Agent 记得"上次发生了什么"，实现跨会话的连续性。

"什么该记"原则（课程 PPT p70）：
- 只记**稳定、客观、经确认**的信息：学科强弱、运动/时间偏好、
  用户确认过的反馈结论；
- **不记**情绪化表达（"今天心情差""讨厌数学"之类的随口抱怨）、
  一次性上下文和未经核实的推测——这些信息噪声大且可能过时，
  记进长期记忆反而会误导后续会话。

memory.json 为运行时生成文件，已加入 .gitignore，不入库。
"""

import json
import os
from datetime import datetime

from context_store import STUDENTS_MEMORY_PATH


class MemoryStore:
    """跨会话记忆仓库：长期画像（profile）+ 情景摘要（episodes）。"""

    # 记忆文件结构：
    # {
    #   "profile":  {薄弱学科/运动/时间偏好 ...},        # 长期记忆
    #   "episodes": [{session_id, timestamp, summary, feedback}, ...]  # 情景记忆
    # }
    def __init__(self, path: str = "memory.json") -> None:
        self.path = path
        # 内存中的记忆状态，首次运行时为空模板
        self.state: dict = {"profile": {}, "episodes": []}
        self._load()

    # ------------------------------------------------------------------
    # 持久化：读 / 写
    # ------------------------------------------------------------------
    def _load(self) -> None:
        """从磁盘读入记忆；文件不存在或损坏时保留空模板（防御性）。"""
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # 只认这两个键，忽略多余字段，保证结构稳定
                self.state["profile"] = data.get("profile") or {}
                self.state["episodes"] = data.get("episodes") or []
        except (json.JSONDecodeError, OSError):
            # 文件损坏时静默回退空记忆，不让一次坏文件毁掉整个系统
            pass

    def _save(self) -> None:
        """把记忆写回磁盘（utf-8、indent=2、保留中文）。"""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 长期记忆：用户画像
    # ------------------------------------------------------------------
    def load_profile(self) -> dict:
        """读取长期记忆——用户画像（薄弱学科/运动/时间偏好等）。

        返回副本（浅拷贝），调用方如需写回请用 update_profile()，
        不要原地修改返回值后指望自动保存。
        """
        return dict(self.state["profile"])

    def update_profile(self, profile: dict) -> None:
        """合并式更新用户画像：新键覆盖旧键，未提及的旧键保持不变，自动保存。

        为什么是"合并"而不是"整体覆盖"：跨会话时用户可能只补充了一个偏好，
        若整体覆盖会把之前记住的画像信息冲掉——长期记忆应当"增量积累"。
        """
        self.state["profile"].update(profile or {})
        self._save()

    # ------------------------------------------------------------------
    # 情景记忆：会话摘要
    # ------------------------------------------------------------------
    def add_episode(self, session_id: str, summary: str, feedback: str = "") -> None:
        """追加一条情景记忆：本次会话干了什么、结论如何、用户反馈如何。

        每条记录：{"session_id", "timestamp", "summary", "feedback"}。
        feedback 只应记录**用户确认过**的反馈（如"计划可行"），
        不记录情绪化吐槽（见模块 docstring 的"什么该记"原则）。
        """
        self.state["episodes"].append(
            {
                "session_id": session_id,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "summary": summary,
                "feedback": feedback,
            }
        )
        self._save()

    def get_episodes(self, limit: int = 5) -> list[dict]:
        """取最近 limit 条情景记忆（新的在前），供展示或注入 prompt。"""
        return list(reversed(self.state["episodes"]))[:limit]

    # ------------------------------------------------------------------
    # 注入 prompt 用：把记忆压成一段短文本
    # ------------------------------------------------------------------
    def build_memory_context(self) -> str:
        """把长期画像 + 最近情景拼成可注入 prompt 的文本（≤300 字）。

        设计意图：新会话开始时把这段文本塞进 Agent 的上下文，
        Agent 就能"记得"这个学生是谁、上次聊到哪儿，而不是从零问起。
        控制长度是为了遵守 CRISP 原则——记忆是补充，不是主食。
        """
        parts: list[str] = []
        profile = self.state["profile"]
        if profile:
            画像行 = "；".join(f"{k}={v}" for k, v in profile.items() if v)
            parts.append(f"【长期记忆·学生画像】{画像行}")
        episodes = self.get_episodes(limit=3)
        if episodes:
            情景行 = " / ".join(
                f"{e.get('timestamp', '')[:10]} {e.get('summary', '')}"
                + (f"（反馈：{e['feedback']}）" if e.get("feedback") else "")
                for e in episodes
            )
            parts.append(f"【情景记忆·最近会话】{情景行}")
        文本 = "\n".join(parts)
        return 文本[:300]  # 硬上限 300 字，防止记忆膨胀挤占上下文窗口

    # ------------------------------------------------------------------
    # 清空
    # ------------------------------------------------------------------
    def clear(self) -> None:
        """清空全部记忆（画像 + 情景）并保存；前端【清除记忆】按钮使用。"""
        self.state = {"profile": {}, "episodes": []}
        self._save()


# ---------------------------------------------------------------------------
# 学生记忆库：按学生姓名组织的跨会话长期记忆（students_memory.json）
#
# 供前端 app.py 的"学生记忆库"Tab 与侧边栏 Memory 提示卡使用。
# JSON 结构（以学生姓名为键）：
# {
#   "小明": {
#     "最新画像": {...},            # 最近一次会话的学生画像（整体覆盖）
#     "会话次数": 2,                 # 累计规划次数
#     "首次使用": "2024-.. ..:..",   # 第一次记录时间
#     "最近活动": "2024-.. ..:..",   # 最近一次记录时间
#     "histories": [                 # 每次会话追加一条摘要
#       {"时间": "...", "诊断摘要": "...", "计划摘要": "..."}
#     ]
#   }
# }
# students_memory.json 为运行时生成文件，已加入 .gitignore，不入库。
# ---------------------------------------------------------------------------
def _load_students_memory(path: str = STUDENTS_MEMORY_PATH) -> dict:
    """读取学生记忆库；文件不存在或损坏时返回空 dict（防御性）。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def record_student_session(
    name: str,
    profile: dict,
    诊断摘要: str,
    计划摘要: str,
    path: str = STUDENTS_MEMORY_PATH,
) -> None:
    """记录一次学习规划会话到学生记忆库（按学生姓名组织）。

    - 同一学生再次规划时：会话次数 +1、最新画像整体覆盖、histories 追加一条；
    - 首次使用时登记"首次使用"时间，"最近活动"每次更新。
    """
    name = (name or "").strip()
    if not name:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = _load_students_memory(path)
    entry = data.get(name) or {}
    histories = entry.get("histories")
    if not isinstance(histories, list):
        histories = []
    histories.append({"时间": now, "诊断摘要": 诊断摘要 or "", "计划摘要": 计划摘要 or ""})
    data[name] = {
        "最新画像": dict(profile or {}),
        "会话次数": int(entry.get("会话次数", 0) or 0) + 1,
        "首次使用": entry.get("首次使用") or now,
        "最近活动": now,
        "histories": histories,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
