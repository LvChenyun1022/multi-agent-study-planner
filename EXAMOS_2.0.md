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

## 掌握度估计（第一版）

```
M̂ = Σ w_j y_j / Σ w_j
w_j = w_reliability · w_recency · w_difficulty
```

- reliability：exam 1.2 / mock_exam 1.1 / quiz 1.0 / assignment 0.8 / 自评 0.3
- recency：指数衰减，半衰期 14 天
- difficulty：0.5 + difficulty（无标注取 1.0）
- 输出：mastery、Kish 有效样本量、置信等级（none/low/medium/high）、证据数

## 测试结果

`python -m unittest discover` → 54/54 通过（22 项 1.0 遗留 + 32 项新增）。

## 明确的边界（未实现）

- 未实现从日历/诊断 Agent 自动构建 exam_plan（沿自 1.0 集成报告的边界）；
- 未实现 Course Intelligence（文档解析/OCR/知识图谱，属 Phase 2）；
- 未实现 Level 1（学期级）与 Level 3（即时）决策；
- 掌握度模型为证据加权统计模型，未引入 BKT；
- demo_slice.py 演示数据全部为【模拟】数据。
