# -*- coding: utf-8 -*-
"""Orchestrator：状态机调度器 + CRISP 上下文工程完整落地。

职责（对应课程术语 Context Engineering / 多 Agent 编排）：
1. 按固定顺序 ``TASK_ORDER`` 调度三个 Agent（状态机：pending → running → done，
   并扩展了 failed / skipped 两种终态）；
2. **调度健壮性**：
   - 失败重试：``agent.run(context)`` 抛异常时自动重试 1 次，仍失败则该任务
     标记 ``status="failed"`` 并记录 ``error``，依赖它的下游 Agent 标记
     ``status="skipped"`` 跳过，整个 run_session 不崩溃、正常返回；
   - 条件分支（扩展点）：user_profile 含 ``"只要计划": true`` 或
     ``"skip_tutor": true`` 时跳过辅导Agent（标记 skipped）；
3. **CRISP 五原则完整落地**（逐条见代码内注释，是本类的答辩核心）：
   - S(Select)   ：``select_context`` 按需切片，每个 Agent 只拿最小必要上下文；
   - C(Compress) ：``_压缩诊断报告`` 诊断报告超 400 字先压缩再给辅导Agent；
   - R(Rank)     ：诊断Agent的 rag_sources 已在 MCP 层按 TF-IDF 余弦相似度
                   排序（见 mcp_server.py），此处记录 rag_top_k 佐证；
   - I(Isolate)  ：三 Agent 互相看不到对方的 tool_calls 细节，只看到 output；
   - P(Purge)    ：``_清理瞬时状态`` 会话结束时清理残留瞬时字段；
4. Token 估算与 Context 统计：每个任务 finish 时记录 ``token_cost``，
   会话结束写入顶层 ``context_stats``（full_chars vs crisp_chars），
   供前端展示"CRISP 省了多少上下文"。
5. **DAG 并行调度（同学B扩展）**：构造时传入 ``task_plan``（DAG 节点列表）
   与 ``max_workers`` 即切换到并行模式——用 ``ThreadPoolExecutor`` 调度，
   无依赖且 condition 满足的节点并行执行；依赖节点 failed/skipped 则下游
   skipped；每个节点按自己的 ``max_retries`` 指数退避重试（0.5s/1s/2s…）。
   ``task_plan=None``（默认）时走原有串行逻辑，行为与旧版完全一致。

   task_plan 节点格式::

       {"name": "诊断Agent",          # Agent 名，须能在 agents 字典中找到
        "depends_on": ["上游Agent"],  # 依赖的 Agent 名列表，可为空
        "max_retries": 2,             # 失败重试次数（不含首次运行）
        "condition": callable 或 None} # condition(outputs, store) -> bool；
                                       # 返回 False 则本节点 skipped，None 视为 True
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time

# 调度顺序：先诊断（摸清问题）→ 再规划（安排时间）→ 后辅导（具体引导）
TASK_ORDER = ["诊断Agent", "规划Agent", "辅导Agent"]

# 任务依赖表（调度扩展点）：新增 Agent 时在此声明它依赖哪些上游。
# 上游 failed / skipped 时，下游自动 skipped，避免"拿着坏数据继续跑"。
TASK_DEPENDENCIES: dict[str, list[str]] = {
    "诊断Agent": [],
    "规划Agent": ["诊断Agent"],              # 排计划要结合薄弱点
    "辅导Agent": ["诊断Agent", "规划Agent"],  # 引导要针对薄弱点、贴合计划
}

# CRISP —— C(Compress) 的触发阈值：诊断报告超过该字符数才压缩
_COMPRESS阈值 = 400


def _估算token(文本: str) -> int:
    """粗略估算一段文本的 token 数（**经验估算，非精确分词**，仅供统计展示）。

    估算规则：
    - 中文等 CJK 字符：约 1.8 个字符 ≈ 1 token（经验值）；
    - 英文/数字：按"单词"计，连续字母数字串算 1 token；
    - 其余符号（标点、空白）开销小，忽略；结果取整。
    """
    if not 文本:
        return 0
    cjk数 = sum(
        1 for 字 in 文本
        if "一" <= 字 <= "鿿"        # CJK 统一表意文字
        or "　" <= 字 <= "〿"      # CJK 标点
        or "＀" <= 字 <= "￯"      # 全角字符
    )
    英文词数 = len(re.findall(r"[A-Za-z0-9]+", 文本))
    return round(cjk数 / 1.8) + 英文词数


class Orchestrator:
    """多 Agent 会话调度器（状态机 + CRISP 上下文工程）。"""

    def __init__(self, agents: dict, store, llm=None,
                 task_plan: list[dict] | None = None, max_workers: int = 3) -> None:
        """
        agents     : {"Agent名": BaseAgent 实例} 的字典（duck-typing，便于替换实现）。
        store      : ContextStore 实例，负责共享上下文的读写与持久化。
        llm        : 可选。若提供（实现 chat(system, user) 协议），CRISP 的
                     C(Compress) 环节会用它做智能摘要；为 None 时降级为规则截取。
        task_plan  : 可选。DAG 节点列表（格式见模块 docstring）。为 None/空
                     时走原有串行逻辑（TASK_ORDER），行为与旧版完全一致；
                     非空时切换到 DAG 并行调度模式。
        max_workers: 并行模式的线程池并发度（默认 3，仅 task_plan 非空时生效）。
        """
        self.agents = agents
        self.store = store
        self.llm = llm
        self.task_plan = task_plan
        self.max_workers = max_workers
        # 并行模式下保护 context_stats 列表追加的锁（store 自身的并发安全
        # 由 ContextStore 内部 RLock 保证）。
        self._stats锁 = threading.Lock()

    # ------------------------------------------------------------------
    # CRISP —— S(Select)：按需切片，只给"刚刚好"的上下文
    # ------------------------------------------------------------------
    def select_context(self, agent_name: str) -> dict:
        """为指定 Agent 挑选"刚刚好"的上下文。

        【CRISP · S(Select)】按需提供、最少够用：
        - 诊断Agent：只需要 user_profile（它负责"发现问题"，无需看别人结论）；
        - 规划Agent：user_profile + 诊断 output（排计划要结合薄弱点）；
        - 辅导Agent：诊断 output + 计划 output（引导要针对薄弱点、
          且与计划节奏一致；**不给** user_profile 之外的隐私细节，
          也**不塞**全量 task_chain，避免无关历史稀释注意力）。

        【CRISP · I(Isolate)】注意这里只透传各 Agent 的 ``output`` 字段：
        各 Agent 的 tool_calls / rag_sources 等细节虽然存在 task_chain 里
        （供前端展示），但**不会**被切片给其他 Agent —— 三 Agent 互相
        看不到对方的工具调用细节，实现上下文隔离、防止串扰。
        """
        snap = self.store.snapshot()
        产出 = {
            任务.get("agent"): 任务.get("output", "")
            for 任务 in snap.get("task_chain", [])
        }

        if agent_name == "诊断Agent":
            return {"user_profile": snap.get("user_profile", {})}
        if agent_name == "规划Agent":
            return {
                "user_profile": snap.get("user_profile", {}),
                "诊断Agent_output": 产出.get("诊断Agent", ""),
            }
        if agent_name == "辅导Agent":
            return {
                "诊断Agent_output": 产出.get("诊断Agent", ""),
                "规划Agent_output": 产出.get("规划Agent", ""),
            }
        # 兜底：未知 Agent 给空上下文，而不是全量（宁可少给）
        return {}

    # ------------------------------------------------------------------
    # CRISP —— C(Compress)：超长诊断报告先压缩再传给辅导Agent
    # ------------------------------------------------------------------
    def _压缩诊断报告(self, context: dict) -> tuple[dict, bool, dict | None]:
        """【CRISP · C(Compress)】辅导Agent的输入中，诊断报告超过
        ``_COMPRESS阈值``（400 字）时先压缩再传入，避免长报告稀释注意力。

        压缩策略（双档降级）：
        - 有 llm：让 llm 把报告"压缩成100字内要点"（智能摘要）；
        - 无 llm（或 llm 调用失败）：降级为规则截取——首行 + 含"结论"的行。

        返回 (新context, 是否压缩, 压缩信息)；压缩信息记录压缩前后长度，
        会写入该任务的 extra 供前端展示。
        """
        报告 = context.get("诊断Agent_output", "") or ""
        if len(报告) <= _COMPRESS阈值:
            return context, False, None

        原长 = len(报告)
        压缩后 = ""
        if self.llm is not None:
            try:
                候选 = self.llm.chat(
                    "你是一名信息压缩助手，只输出压缩后的要点文本，不要解释。",
                    f"把以下诊断报告压缩成100字内要点：\n{报告}",
                ).strip()
                # 防御：摘要为空或反而更长时视为压缩失败，走降级
                if 候选 and len(候选) < 原长:
                    压缩后 = 候选
            except Exception as e:  # noqa: BLE001 - 压缩失败不应中断会话
                print(f"[Orchestrator] ⚠ LLM 压缩失败（{type(e).__name__}: {e}），降级为规则截取")

        if not 压缩后:
            # 无 llm 的降级策略：截取首行 + 结论行
            行们 = [行.strip() for 行 in 报告.splitlines() if 行.strip()]
            首行 = 行们[0] if 行们 else ""
            结论行们 = [行 for 行 in 行们 if "结论" in 行]
            压缩后 = "\n".join([首行, *结论行们]).strip() or 报告[:100]

        新context = dict(context)
        新context["诊断Agent_output"] = 压缩后
        信息 = {"compress_before_chars": 原长, "compress_after_chars": len(压缩后)}
        print(f"[Orchestrator] CRISP-C(Compress)：诊断报告 {原长} 字 → 压缩后 {len(压缩后)} 字")
        return 新context, True, 信息

    # ------------------------------------------------------------------
    # 调度健壮性：带重试的 Agent 运行
    # ------------------------------------------------------------------
    def _运行带重试(self, agent, context: dict, agent_name: str) -> tuple[dict | None, str | None]:
        """运行 Agent，抛异常时重试 1 次。

        返回 (result, None) 成功；(None, 错误描述) 表示重试后仍失败。
        重试仍失败不向上抛——由调用方标记 failed 并跳过下游，保证
        run_session 整体不崩溃。
        """
        try:
            return agent.run(context), None
        except Exception as e1:  # noqa: BLE001 - 调度器要兜住一切 Agent 异常
            print(
                f"[Orchestrator] ⚠ {agent_name} 运行异常"
                f"（{type(e1).__name__}: {e1}），重试 1 次 …"
            )
            try:
                return agent.run(context), None
            except Exception as e2:  # noqa: BLE001
                错误 = f"{type(e2).__name__}: {e2}"
                print(f"[Orchestrator] ✘ {agent_name} 重试仍失败：{错误}，标记 failed")
                return None, 错误

    # ------------------------------------------------------------------
    # CRISP —— P(Purge)：会话结束清理瞬时字段
    # ------------------------------------------------------------------
    def _清理瞬时状态(self) -> None:
        """【CRISP · P(Purge)】run_session 结束时清理瞬时/残留状态，
        保证落盘的 context_store.json 是一份"干净"的会话档案：
        - 残留的 pending_approval 置 null（审批单是瞬时的，跨会话无意义）；
        - task_chain 里仍停留在 running 状态的条目兜底修正为 failed
          （理论上不会发生，防御 Agent 异常退出没走 finish/fail 路径）。
        """
        snap = self.store.snapshot()
        if snap.get("pending_approval"):
            print("[Orchestrator] CRISP-P(Purge)：清理残留的 pending_approval → null")
            self.store.set_pending_approval(None)
        for 任务 in snap.get("task_chain", []):
            if 任务.get("status") == "running":
                print(f"[Orchestrator] CRISP-P(Purge)：{任务.get('agent')} 残留 running，兜底修正为 failed")
                self.store.update_task(
                    任务["agent"], "failed",
                    error="会话结束时仍处于 running，已由 Purge 兜底修正",
                )

    # ------------------------------------------------------------------
    # 会话主循环
    # ------------------------------------------------------------------
    def run_session(self, session_id: str, user_profile: dict) -> dict:
        """跑一次完整会话：reset → 调度执行 → Purge → 返回快照。

        调度模式：
        - ``task_plan`` 为 None/空：串行模式（TASK_ORDER 固定顺序），行为与旧版一致；
        - ``task_plan`` 非空：DAG 并行模式（ThreadPoolExecutor + 依赖/条件调度）。

        健壮性保证：任一 Agent 失败（重试后仍抛异常）只会让该任务 failed、
        依赖它的下游 skipped，run_session 本身绝不崩溃、正常返回快照。
        """
        if self.task_plan:
            return self._并行会话(session_id, user_profile)
        return self._串行会话(session_id, user_profile)

    def _串行会话(self, session_id: str, user_profile: dict) -> dict:
        """串行模式主循环：reset → 逐 Agent start/run/finish → Purge → 返回快照。

        即旧版 run_session 的原始实现，task_plan=None 时走这里，行为不变。
        """
        self.store.reset(session_id, user_profile)
        print(f"[Orchestrator] 会话 {session_id} 开始，任务顺序：{' → '.join(TASK_ORDER)}")

        失败集合: set[str] = set()
        跳过集合: set[str] = set()
        context_stats: list[dict] = []  # 每个 Agent 一条上下文统计（会话末写入 store）

        for agent_name in TASK_ORDER:
            # ----------------------------------------------------------
            # 条件分支（调度扩展点）：用户"只要计划"时跳过辅导Agent。
            # 支持两种写法："只要计划": true（中文业务语义）或
            # "skip_tutor": true（通用开关）。新增分支条件时在此扩展。
            # ----------------------------------------------------------
            if agent_name == "辅导Agent" and (
                user_profile.get("只要计划") or user_profile.get("skip_tutor")
            ):
                原因 = "用户档案标记「只要计划」/ skip_tutor，按条件分支跳过辅导"
                self.store.start_task(agent_name, input_note="由 Orchestrator 调度")
                self.store.update_task(agent_name, "skipped", skip_reason=原因)
                跳过集合.add(agent_name)
                context_stats.append(self._统计条目(agent_name, crisp_chars=0, compressed=False))
                print(f"[Orchestrator] ⤼ {agent_name} 条件分支命中：{原因}")
                continue

            # ----------------------------------------------------------
            # 依赖检查：上游 failed / skipped 时，下游跳过（不拿坏数据跑）
            # ----------------------------------------------------------
            坏上游 = [
                dep for dep in TASK_DEPENDENCIES.get(agent_name, [])
                if dep in 失败集合 or dep in 跳过集合
            ]
            if 坏上游:
                原因 = f"上游 {'、'.join(坏上游)} 失败/被跳过，本任务无法获得有效输入"
                self.store.start_task(agent_name, input_note="由 Orchestrator 调度")
                self.store.update_task(agent_name, "skipped", skip_reason=原因)
                跳过集合.add(agent_name)
                context_stats.append(self._统计条目(agent_name, crisp_chars=0, compressed=False))
                print(f"[Orchestrator] ⤼ {agent_name} 跳过：{原因}")
                continue

            agent = self.agents[agent_name]
            self.store.start_task(agent_name, input_note="由 Orchestrator 调度")
            print(f"[Orchestrator] ▶ {agent_name} 开始运行 …")

            # ----- Context 统计：CRISP 前后对比（先算基线，再切片） -----
            # full_chars = "粗暴全塞"基线：假设不做 CRISP，把当时可用的全部信息
            # （user_profile + 当时 task_chain 全部记录）序列化塞给 Agent 的字符数。
            snap = self.store.snapshot()
            full_chars = len(json.dumps(
                {"user_profile": snap.get("user_profile", {}),
                 "task_chain": snap.get("task_chain", [])},
                ensure_ascii=False,
            ))

            context = self.select_context(agent_name)  # CRISP-S：只给必要上下文

            # CRISP-C：辅导Agent的诊断报告超阈值先压缩
            compressed = False
            压缩信息 = None
            if agent_name == "辅导Agent":
                context, compressed, 压缩信息 = self._压缩诊断报告(context)

            context_json = json.dumps(context, ensure_ascii=False)
            context_stats.append({
                "agent": agent_name,
                "full_chars": full_chars,
                "crisp_chars": len(context_json),  # 实际 select_context 后传入的字符数
                "compressed": compressed,
            })

            # ----- 运行（带 1 次失败重试） -----
            result, 错误 = self._运行带重试(agent, context, agent_name)
            if result is None:
                # 重试仍失败：标记 failed，记录 error；后续依赖它的 Agent 会被跳过
                self.store.update_task(
                    agent_name, "failed",
                    error=错误,
                    token_cost=_估算token(context_json),  # 只消耗了输入，没有产出
                )
                失败集合.add(agent_name)
                continue

            output = result.get("output", "")
            # 除 output 外的附加信息（rag_sources/tool_calls/skill_used）存进 extra
            extra = {k: v for k, v in result.items() if k != "output"}
            # token_cost：该 Agent 输入 context + 输出 output 的估算 token 总和
            extra["token_cost"] = _估算token(context_json) + _估算token(output)
            if 压缩信息:
                extra.update(压缩信息)  # 记录压缩前后长度
            if agent_name == "诊断Agent":
                # CRISP —— R(Rank)：rag_sources 已在 MCP 层（mcp_server.py）
                # 用 TF-IDF 向量 + 余弦相似度排好序，诊断Agent只取 top_k；
                # 这里记录 rag_top_k 作为 Rank 生效的佐证。
                extra["rag_top_k"] = len(extra.get("rag_sources") or [])
            self.store.finish_task(agent_name, output, extra=extra or None)
            print(
                f"[Orchestrator] ✔ {agent_name} 完成，输出 {len(output)} 字"
                f"（估算 token_cost≈{extra['token_cost']}）"
            )

            # 安全提示：若运行中触发了敏感工具审批，打印出来等待人工处理
            pending = self.store.snapshot().get("pending_approval")
            if pending:
                print(
                    f"[Orchestrator] ⚠ 检测到待审批操作："
                    f"{pending.get('agent')} 请求调用 {pending.get('tool')}，"
                    "请人工批准或拒绝后再继续。"
                )

        # CRISP —— P(Purge)：会话收尾，清理瞬时字段
        self._清理瞬时状态()
        # 写入 context_stats：供前端展示"CRISP 省了多少上下文"
        self.store.set("context_stats", context_stats)
        print(f"[Orchestrator] 会话 {session_id} 结束，context_stats 已写入。")
        return self.store.snapshot()

    # ------------------------------------------------------------------
    # DAG 并行调度（task_plan 非空时启用）
    # ------------------------------------------------------------------
    def _并行会话(self, session_id: str, user_profile: dict) -> dict:
        """并行模式主循环：按 task_plan 声明的 DAG 用线程池调度。

        调度规则：
        - 无未满足依赖（全部上游 done）且 condition 满足的节点即可提交线程池
          并行执行，并发度由 ``max_workers`` 控制；
        - 依赖节点 failed/skipped → 本节点 skipped（不拿坏数据跑）；
        - ``condition(outputs, store)`` 返回 False → 本节点 skipped 并记录
          skip_reason；condition 为 None 视为 True；condition 自身抛异常
          时防御性视为 False（宁可跳过，不中断会话）；
        - 出现环或依赖了不存在的节点（永远无法就绪）→ 兜底 skipped，不死锁。
        """
        self.store.reset(session_id, user_profile)
        节点表 = {节点["name"]: 节点 for 节点 in self.task_plan}
        print(
            f"[Orchestrator] 会话 {session_id} 开始（DAG 并行模式，"
            f"max_workers={self.max_workers}），节点：{'、'.join(节点表)}"
        )

        状态: dict[str, str] = {}      # name -> done / failed / skipped
        outputs: dict[str, str] = {}   # name -> output 文本（condition 与统计用）
        context_stats: list[dict] = []
        待调度 = set(节点表)
        futures: dict = {}             # Future -> agent 名

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while 待调度 or futures:
                # ---------- 提交所有"已就绪"的节点 ----------
                for name in sorted(待调度):
                    节点 = 节点表[name]
                    依赖 = list(节点.get("depends_on") or [])

                    # 依赖检查：上游 failed/skipped → 本节点 skipped
                    坏上游 = [d for d in 依赖 if 状态.get(d) in ("failed", "skipped")]
                    if 坏上游:
                        原因 = f"上游 {'、'.join(坏上游)} 失败/被跳过，本任务无法获得有效输入"
                        self._并行跳过节点(name, 原因, context_stats)
                        状态[name] = "skipped"
                        待调度.discard(name)
                        continue

                    if not all(状态.get(d) == "done" for d in 依赖):
                        continue  # 依赖未全部完成，等待下一轮

                    # 条件分支：condition(outputs, store) 返回 False → skipped
                    condition = 节点.get("condition")
                    if condition is not None:
                        try:
                            满足 = bool(condition(outputs, self.store))
                        except Exception as e:  # noqa: BLE001 - 条件异常不中断会话
                            满足 = False
                            原因 = f"condition 评估异常（{type(e).__name__}: {e}），按 False 处理"
                            self._并行跳过节点(name, 原因, context_stats)
                            状态[name] = "skipped"
                            待调度.discard(name)
                            continue
                        if not 满足:
                            self._并行跳过节点(
                                name, "condition(outputs, store) 返回 False，按条件分支跳过",
                                context_stats,
                            )
                            状态[name] = "skipped"
                            待调度.discard(name)
                            continue

                    agent = self.agents.get(name)
                    if agent is None:  # 防御：计划里声明了但 agents 字典里没有
                        self._并行跳过节点(
                            name, f"agents 字典中找不到名为 {name} 的 Agent 实例",
                            context_stats,
                        )
                        状态[name] = "skipped"
                        待调度.discard(name)
                        continue

                    future = pool.submit(
                        self._并行执行任务, name, agent, 节点, context_stats,
                    )
                    futures[future] = name
                    待调度.discard(name)

                # ---------- 等待任一任务完成，推进 DAG ----------
                if futures:
                    完成, _ = concurrent.futures.wait(
                        futures, return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in 完成:
                        name = futures.pop(future)
                        结果状态, 输出 = future.result()  # 任务内部已兜住异常
                        状态[name] = 结果状态
                        if 输出 is not None:
                            outputs[name] = 输出
                elif 待调度:
                    # 没有在跑的任务却还有节点未就绪 → 环 / 依赖了不存在的节点
                    for name in sorted(待调度):
                        缺失 = [
                            d for d in (节点表[name].get("depends_on") or [])
                            if 状态.get(d) != "done"
                        ]
                        原因 = f"依赖 {'、'.join(缺失)} 永远无法就绪（不存在或存在循环依赖）"
                        self._并行跳过节点(name, 原因, context_stats)
                        状态[name] = "skipped"
                    待调度.clear()

        # CRISP —— P(Purge)：会话收尾，清理瞬时字段
        self._清理瞬时状态()
        # 写入 context_stats：供前端展示"CRISP 省了多少上下文"
        self.store.set("context_stats", context_stats)
        print(f"[Orchestrator] 会话 {session_id} 结束，context_stats 已写入。")
        return self.store.snapshot()

    def _并行跳过节点(self, name: str, 原因: str, context_stats: list[dict]) -> None:
        """把某个节点标记为 skipped（登记 task_chain + context_stats + 日志）。"""
        self.store.start_task(name, input_note="由 Orchestrator 调度")
        self.store.update_task(name, "skipped", skip_reason=原因)
        with self._stats锁:
            context_stats.append(self._统计条目(name, crisp_chars=0, compressed=False))
        print(f"[Orchestrator] ⤼ {name} 跳过：{原因}")

    def _并行执行任务(self, name: str, agent, 节点: dict,
                      context_stats: list[dict]) -> tuple[str, str | None]:
        """线程池工作函数：运行单个节点（带指数退避重试），返回 (状态, output)。

        状态为 "done"（成功）或 "failed"（重试后仍失败）；失败时 output 为 None。
        Agent 异常在本函数内全部兜住，Future 不会抛异常。
        """
        self.store.start_task(name, input_note="由 Orchestrator 调度")
        print(f"[Orchestrator] ▶ {name} 开始运行 …（并行）")

        # ----- Context 统计：只对 select_context 认识的三个内置 Agent 统计 -----
        # 未知 Agent（如学情摘要Agent）select_context 返回空上下文，统计无意义，
        # 防御性跳过、不报错。
        内置Agent = {"诊断Agent", "规划Agent", "辅导Agent"}
        snap = self.store.snapshot()
        full_chars = len(json.dumps(
            {"user_profile": snap.get("user_profile", {}),
             "task_chain": snap.get("task_chain", [])},
            ensure_ascii=False,
        ))

        context = self.select_context(name)  # CRISP-S：只给必要上下文

        # CRISP-C：辅导Agent的诊断报告超阈值先压缩（与串行模式一致）
        compressed = False
        压缩信息 = None
        if name == "辅导Agent":
            context, compressed, 压缩信息 = self._压缩诊断报告(context)

        context_json = json.dumps(context, ensure_ascii=False)
        if name in 内置Agent:
            with self._stats锁:
                context_stats.append({
                    "agent": name,
                    "full_chars": full_chars,
                    "crisp_chars": len(context_json),
                    "compressed": compressed,
                })

        # ----- 运行（按节点 max_retries 指数退避重试） -----
        result, 错误 = self._并行运行带重试(
            agent, context, name, max_retries=int(节点.get("max_retries") or 0),
        )
        if result is None:
            self.store.update_task(
                name, "failed",
                error=错误,
                token_cost=_估算token(context_json),  # 只消耗了输入，没有产出
            )
            return "failed", None

        output = result.get("output", "")
        extra = {k: v for k, v in result.items() if k != "output"}
        extra["token_cost"] = _估算token(context_json) + _估算token(output)
        if 压缩信息:
            extra.update(压缩信息)
        if name == "诊断Agent":
            # CRISP —— R(Rank)：记录 rag_top_k 作为 Rank 生效的佐证
            extra["rag_top_k"] = len(extra.get("rag_sources") or [])
        self.store.finish_task(name, output, extra=extra or None)
        print(
            f"[Orchestrator] ✔ {name} 完成，输出 {len(output)} 字"
            f"（估算 token_cost≈{extra['token_cost']}）"
        )

        # 安全提示：若运行中触发了敏感工具审批，打印出来等待人工处理
        pending = self.store.snapshot().get("pending_approval")
        if pending:
            print(
                f"[Orchestrator] ⚠ 检测到待审批操作："
                f"{pending.get('agent')} 请求调用 {pending.get('tool')}，"
                "请人工批准或拒绝后再继续。"
            )
        return "done", output

    def _并行运行带重试(self, agent, context: dict, agent_name: str,
                       max_retries: int) -> tuple[dict | None, str | None]:
        """并行模式的带重试运行：最多重试 ``max_retries`` 次，指数退避。

        退避节奏：0.5s → 1s → 2s → 4s …（0.5 * 2^已重试次数）。
        返回 (result, None) 成功；(None, 错误描述) 表示重试后仍失败。
        """
        for 尝试 in range(max_retries + 1):
            try:
                return agent.run(context), None
            except Exception as e:  # noqa: BLE001 - 调度器要兜住一切 Agent 异常
                错误 = f"{type(e).__name__}: {e}"
                if 尝试 < max_retries:
                    等待 = 0.5 * (2 ** 尝试)  # 指数退避：0.5s / 1s / 2s / …
                    print(
                        f"[Orchestrator] ⚠ {agent_name} 第 {尝试 + 1} 次运行异常"
                        f"（{错误}），{等待:.1f}s 后进行第 {尝试 + 2} 次尝试 …"
                    )
                    time.sleep(等待)
                else:
                    print(
                        f"[Orchestrator] ✘ {agent_name} 重试 {max_retries} 次后仍失败："
                        f"{错误}，标记 failed"
                    )
        return None, 错误

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _统计条目(self, agent_name: str, crisp_chars: int, compressed: bool) -> dict:
        """生成一条 context_stats 记录（被跳过的 Agent 也登记，crisp_chars=0）。

        full_chars 的定义（"粗暴全塞"基线）：user_profile + 当时 task_chain
        全部记录的 JSON 序列化字符数——即假设不做 CRISP，把所有可用信息
        一股脑塞给该 Agent 时的上下文长度。
        """
        snap = self.store.snapshot()
        full_chars = len(json.dumps(
            {"user_profile": snap.get("user_profile", {}),
             "task_chain": snap.get("task_chain", [])},
            ensure_ascii=False,
        ))
        return {
            "agent": agent_name,
            "full_chars": full_chars,
            "crisp_chars": crisp_chars,
            "compressed": compressed,
        }
