# ExamOS 2.0 · Phase 1 实施说明

分支：`feature/examos-2.0-intelligence`（基线：`feature/examos-scheduler-integration` CI 绿状态）

## 本阶段交付

按 2.0 升级方案的「纵向切片」原则实施，一条链路跑通：

```
登记课程档案 → 记录学习事件（含纠正）→ 重建学生画像 →
桥接生成 exam_plan → 调用 1.0 run_exam_plan() 排程
```

新增目录：

| 目录 | 内容 |
| --- | --- |
| `schemas/` | StudentProfile、LearningEvent、CourseSpec/TaskSpec，手工校验（风格对齐 scheduler_adapter） |
| `storage/` | SQLite 表结构 + Repository；learning_events 由触发器强制只追加 |
| `services/` | LearningEventService（记录/纠正/有效视图）、StudentService（档案、选课、画像重建） |
| `intelligence/student/` | mastery_estimator（证据加权掌握度）、profile_builder（画像组装） |
| `intelligence/decision/` | exam_plan_bridge（画像 → run_exam_plan 标准输入） |
| `agents/exam_analysis_agent.py` | 考试分析 Agent：考试范围文本（LLM 抽取）或结构化输入 → CourseSpec，校验与排程入口同源 |
| `tests/` | 32 项新测试（事件溯源 12、建模引擎 13、端到端切片 7） |
| `demo_slice.py` | 一键演示 `python demo_slice.py` |

## 掌握的工程纪律

1. **1.0 兼容契约零改动**：`scheduler.py`、`agents/` 全部文件未触碰；
   `run_exam_plan()` 输入格式原样消费；`weekly_heuristic` /
   `examos_scheduler` 双模式均有测试守护。
2. **事件溯源**：学习事件只追加；纠正通过 `corrects` 新事件完成；
   数据库触发器物理禁止 UPDATE/DELETE。
3. **版本可解释**：`profile_version` 随重建递增，`evidence_version`
   记录计算用到的事件流位置，掌握度变化可重放解释。
4. **证据纪律**：同一 `source_id` 重复练习只算一份证据；
   被纠正/未验证事件不进画像；无证据知识点用中性先验 0.5 并标记
   `is_prior`，不确定性经 `n_diag` 交给排程引擎 U 因子表达。
5. **零新增依赖**：全部标准库实现，CI 与部署环境无新增安装项。

## 掌握度估计（证据校准版）

```
M̂ = Σ w_j y_j / Σ w_j
w_j = R_j · V_j · T_j · D_j
```

- R 可靠性：监考考试 1.2 / 延迟与迁移测试 1.1 / 独立速测 1.05 /
  平时测验 1.0 / 口头验证 0.9 / 作业 0.8 / 自评 0.3
- V 独立性（Phase 1.5 新增）：学生申报 AI 辅助程度的折扣系数，
  none 1.0 / unknown 0.9 / declared_partial 0.5 / declared_heavy 0.15
- T 时间衰减：指数衰减，半衰期 14 天
- D 难度：0.5 + difficulty（无标注取 1.0）
- 输出：mastery、Kish 有效样本量、置信等级、证据数、五态 learning_state

**校准纪律**：R/V 全部数值集中在
`intelligence/assessment/evidence_policy.py`，标注为默认参数，
未经真实数据校准不得宣称精度。

## Phase 1.5：Competency Verification（能力验证）

- `evidence_policy.py`：证据分级与权重的单一真源；区分
  即时/独立/延迟/迁移四层测量方式；
- `competency_triggers.py`：不确定才测的确定性触发规则——
  独立证据不足（τ_eff_n）｜作业与独立能力背离（τ_D，
  AI 辅助作业污染画像的主要信号）｜独立证据过期（τ_T）；
- 新事件类型：`diagnostic_quiz_submitted`、`oral_verification_completed`；
  `payload.assistance` 申报字段。

明确不做（留 Phase 2/3）：AI 文本检测器、题目生成、口头交互界面。
AI 检测只能作为触发信号，不能直接降低掌握度。

## Phase 1.6：Longitudinal Learning（长期保持）

- `retention.py`：
  - 五态知识状态机 unlearned → acquired → independently_retrievable
    → retained → transferable，状态可降级（最新延迟证据 <0.4 打回
    acquired，防止辅导后短期冲高被误判为长期掌握）；
  - 遗忘速率 λ 个体化拟合（≥2 个延迟测量点，ln 线性回归），
    无延迟证据时如实输出 unknown，绝不用即时成绩填充；
  - 复习优先级 I·(1−M)·P_forget·C（知识点级信号，
    不与 D/M/W/U 重复计分）；λ 未知时按证据稀缺度兜底；
- 画像输出新增 `learning_state` 字段。

## Phase 1.7：Community Intelligence（社区证据内核）

定位决策（采纳评审建议）：**B 的产品体验 + A 的智能分析核心**——
学生看到的是自由的校园社区，后台只分析经授权的学习互动。

- `schemas/community.py`：CommunityPost / CommunityReply /
  DiscussionThread（知识讨论链：提问→讨论→修正→延迟验证）/
  CommunityEvidence（六维能力证据）；
- `intelligence/community/evidence_mapper.py`：社区证据 →
  LearningEvent 的确定性转换，三条不可绕过的门禁——
  1. 浏览/点赞/收藏**永不进入画像**（只可用于内容推荐）；
  2. consent=False 的证据直接丢弃（未授权不分析）；
  3. pending/disputed 的证据复核前丢弃（学生异议权优先）；
- 社区证据经 `community_contribution` 事件进入统一证据库
  （R=0.6，弱于正式测评），knowledge_transfer 类证据进入
  transfer 测量层供纵向引擎使用；
- 本层不做内容理解：知识点识别与回答质量评分由上游
  （LLM 分析 Agent 或人工）产出，本层只校验结构与门禁。

明确不做（留 C1–C3 阶段）：论坛前端、信息流、LLM 分析 Agent、
知识挑战推荐、Discourse 部署。

## ProgressMonitorAgent（进度监控 Agent）

- `agents/progress_monitor_agent.py`：四类重规划触发器——
  T1 完成率偏差（单日显著低于计划 / 连续两日低迷）、
  T2 掌握度变化（新测验证据）、T3 约束变化（daily_hours 改变）、
  T4 安全线（临考且必修剩余比例过高）；
- 显著性阈值 + 24 小时冷却（仅拦 completion 类信号，
  manual_request 与约束变化不冷却），避免频繁抖动重规划；
- 输出 execution_feedback 直接适配 `run_exam_plan()` 的 v2 入口；
- 16 项新测试（`tests/test_progress_monitor.py`）。

## 排程引擎坐标系修复

修复 `agents/scheduler_adapter.py` v2 重规划路径的 exam_day
坐标系混用：旧实现把 `course.exam_day` 原地平移（减已流逝天数）后
再交给 `allocate`，而 `allocate` 以绝对日序号判定可排窗口，
`start_day > 0` 时会错误封掉考试前一天。

修复方案：Course 上的 exam_day 恒为绝对日序号，不再原地改写；
新增 `_risk_view()` 构造"剩余天数视图"副本仅供 `risk_scores`
的 D 因子使用（v1 以 start_day 为基准，v2 以 next_day 为基准）。
`scheduler.py` 本体与初赛冻结 demo 输出均未改动
（已与 `chusai-demo-baseline` 逐字比对一致）；新增回归测试
`test_replan_from_later_start_day_keeps_day_before_exam` 锁定该场景。

## 前端五页面重构（决赛交付形态）

新增 Streamlit 多页应用（旧 `app.py` 保留为 legacy 三 Agent 演示）：

| 页面 | 职责 |
| --- | --- |
| `examos_app.py` | 今日仪表盘：今日任务、风险评分、预警、最近反馈 |
| `pages/1_课程与考试.py` | 课程建档：结构化录入 / scope_text 经 LLM 解析（【待确认】后入库），保存一律走 CourseSpec 同源校验 |
| `pages/2_复习计划.py` | 每日时间预算维护、生成/约束变化/手动重排、版本历史与 v1/v2 差异 |
| `pages/3_执行反馈.py` | 按日打卡 → 进度监控判定 → 显著偏差触发重规划；执行日推进 |
| `pages/4_诊断与画像.py` | 诊断 Agent（RAG）、掌握度自评与 n_diag 维护（改动作为重规划信号） |

关键工程决策：

- `examos_state.py` 状态层：JSON 持久化 + 多日推进编排。
  **触发重规划不走 run_exam_plan 的单轮反馈入口**，而是直接编排
  scheduler 原语（apply_day_completion → rollback_future → allocate），
  使执行反馈作用在学生当天实际看到的计划块上；未触发时只做
  done/remaining 簿记，未来计划不变（显著性闸门的意义所在）。
- 总量守恒不变量 `total = done + remaining + 当前计划已分配`
  在每次状态变更后复核，9 项状态层测试覆盖（含坐标系契约、
  按课程冷却、手动请求绕过冷却）。
- LLM 调用全部带加载态与延迟提示；MockLLM 下 scope_text 解析
  诚实报错而非静默降级。
- 冒烟测试 `smoke_pages.py`（AppTest 无头渲染 5 页 × 空/含数据
  两种状态，状态文件重定向到临时目录）。
- 依赖固定 `streamlit>=1.67,<2`（`width="stretch"` 新参数）。

## 六引擎现状

| 引擎 | 状态 |
| --- | --- |
| Student Modeling | ✅ 已实现（证据校准版） |
| Competency Verification | ✅ 核心已实现（触发器 + V 因子） |
| Longitudinal Learning | ✅ 核心已实现（状态机 + 遗忘拟合） |
| Community Intelligence | ✅ 数据内核已实现（证据门禁与转换） |
| Adaptive Decision | ✅ 1.0 排程引擎沿用 |
| Course Intelligence | ❌ Phase 2，最大剩余项 |

## 测试结果

`python -m unittest discover` → 141/141 通过
（22 项 1.0 遗留 + Phase 1 新增 32 项 + Phase 1.5/1.6 新增 25 项
+ Phase 1.7 新增 15 项 + 考试分析 Agent 新增 21 项
+ 进度监控 Agent 新增 16 项 + 坐标系回归 1 项 + 前端状态层 9 项）。

## 明确的边界（未实现）

- 考试分析 Agent 已实现课程侧建档（scope_text → CourseSpec，LLM 抽取
  全部经同源校验）；仍未实现从日历自动推导 daily_hours（净可用时间
  须由上游按真实约束显式提供）；
- 未实现 Course Intelligence（文档解析/OCR/知识图谱，属 Phase 2）；
- 未实现社区前端与 LLM 社区分析 Agent（C1–C3 阶段）；
- 未实现 Level 1（学期级）与 Level 3（即时）决策；
- 掌握度模型为证据加权统计模型，未引入 BKT；
- demo_slice.py 演示数据全部为【模拟】数据；
- evidence_policy.py 中全部权重为默认参数，未经真实数据校准。
