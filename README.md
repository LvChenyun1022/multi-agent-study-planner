# 多Agent学习规划协作系统（骨架版）

课程《大模型与智能体工程实践》大作业 · 方案二骨架。
**离线可跑通**：默认 MockLLM（规则模板）+ 模拟数据（data/ 目录），无需任何 API Key；
各模块预留统一接口，方便后续接入真实 LLM 与真实 API。

> 注意：mock 天气数据只提供**上海**一周天气，demo 与前端的默认城市均为"上海"。

## 快速开始

```bash
pip install -r requirements.txt   # 仅依赖 streamlit（后端逻辑全部用标准库）

python demo.py                    # 命令行全流程演示（无 streamlit 也能跑）
streamlit run app.py              # 启动 Web 前端
```

## 架构图

```
┌────────────────────────────────────────────────────────────┐
│  前端层：app.py（Streamlit） / demo.py（命令行）            │
└──────────────────────────┬─────────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────────┐
│  Orchestrator（orchestrator.py）                            │
│  状态机调度：诊断 → 规划 → 辅导；CRISP 上下文筛选           │
└───────┬──────────────┬──────────────┬──────────────────────┘
        ▼              ▼              ▼
┌──────────────┐┌──────────────┐┌──────────────┐
│  诊断Agent   ││  规划Agent   ││  辅导Agent   │  ← agents/
│  RAG 检索错题││  查日历/天气 ││  加载 Skill  │
└──────┬───────┘└──────┬───────┘└──────┬───────┘
       └───────────────┼───────────────┘
                       ▼ 统一 call_tool（先过 Harness 检查）
┌────────────────────────────────────────────────────────────┐
│  MCP Server（mcp_server.py）                                │
│  工具：rag_search / check_calendar / check_weather /        │
│       send_email（敏感）   ← 数据来自 data/ 模拟文件         │
└──────────────────────────┬─────────────────────────────────┘
                           ▼ 敏感工具
┌────────────────────────────────────────────────────────────┐
│  Harness（harness.py）：审批队列                             │
│  request_approval → pending → approve / reject              │
└────────────────────────────────────────────────────────────┘
        贯穿全程：ContextStore（context_store.py）共享上下文 + JSON 持久化
```

## 课程术语落点表

| 课程术语 | 落点文件 | 说明 |
|---|---|---|
| MCP / Tool | `mcp_server.py`、`tool_schemas.json` | 统一工具接口，Schema 声明工具名/参数/是否敏感 |
| RAG | `agents/diagnose_agent.py` + `data/mock_rag_corpus.json` | 检索错题本/教材语料辅助诊断 |
| Memory / 共享上下文 | `context_store.py` + `context_store.json` | 会话状态、task_chain、审批单持久化 |
| Context Engineering | `orchestrator.py::select_context` | CRISP 筛选：每个 Agent 只拿必要上下文 |
| Skill | `skills/skill_tutor.md` + `agents/tutor_agent.py` | SOP 原文塞入 system prompt |
| Harness（安全层） | `harness.py` + `agents/base_agent.py::call_tool` | 敏感工具（send_email）强制人工审批 |
| CRISP / 调度 | `orchestrator.py` | 状态机：pending → running → done |

## 四人分工扩展点

| 同学 | 负责模块 | 扩展方向 |
|---|---|---|
| 同学 A | `mcp_server.py`、`tool_schemas.json`、`data/` | ✅ 已升级：天气接 Open-Meteo 实时 API、日历接飞书、邮件接 SMTP、RAG 升级为 TF-IDF 余弦相似度检索；复制 `config.example.json` 为 `config.json` 填入凭据即启用（留空自动回退模拟数据） |
| 同学 B | `orchestrator.py` | ✅ 已实现：失败重试（异常重试1次→failed，下游自动 skipped）、条件分支（user_profile 含 `"只要计划"`/`"skip_tutor"` 时跳过辅导Agent）、CRISP 五原则完整落地（S=select_context 切片 / C=诊断报告超400字先压缩再给辅导 / R=MCP层 TF-IDF 排序并记录 rag_top_k / I=三Agent互不可见对方 tool_calls / P=会话末清理瞬时字段）、token 估算与 `context_stats` 统计（full_chars vs crisp_chars）；✅ DAG并行调度（task_plan+max_workers）：无依赖节点经 ThreadPoolExecutor 并行执行、每节点按自己的 max_retries 指数退避重试（0.5s/1s/2s…）、condition(outputs, store) 条件分支（返回 False 记 skipped + skip_reason）、上游 failed/skipped 下游自动 skipped、ContextStore 内置 RLock 保证并发安全，演示入口 `demo_parallel.py`（命令行）/ `app_parallel.py`（Streamlit） |
| 同学 C | `agents/`、`skills/skill_tutor.md`、`llm_client.py` | ✅ 已接真实 LLM：`llm_client.py` 提供 OpenAI 兼容客户端（纯标准库 urllib，超时 60s 重试 1 次）；在 `config.json` 的 `"llm"` 段填入 `base_url` / `api_key` / `model` 即启用（实测模型 `deepseek-v4-pro`），任一缺失自动回退 MockLLM；API 调用失败时单次回退 MockLLM 并打印警告，演示永不中断 |
| 同学 D | `app.py`、`harness.py`、`memory.py` | ✅ 已实现：Memory 三层记忆（`memory.py`：长期画像 + 情景记忆，`memory.json` 运行时生成不入库）、Harness 审批历史（`approval_history` 审计留痕）、前端增强（任务时间线步骤条 / Memory 面板与清除按钮 / 审批历史面板 / CRISP 对比图，防御性兼容 `context_stats` 缺失） |

### 接入真实 LLM（已实现，见 `llm_client.py`）

在 `config.json` 中配置 `"llm"` 段（复制 `config.example.json` 后填写），demo.py 与 app.py 会自动启用真实 LLM：

```json
"llm": {
  "base_url": "https://your-openai-compatible-endpoint/v1",
  "api_key": "sk-...",
  "model": "deepseek-v4-pro"
}
```

```python
from llm_client import create_llm_from_config

llm = create_llm_from_config()  # 配置缺失时返回 None → BaseAgent 自动用 MockLLM
agent = DiagnoseAgent(mcp, store, harness, llm=llm)  # 依赖注入即可
```

回退机制：配置缺失 → 全程 MockLLM；API 超时/报错 → 该次调用回退 MockLLM 并打印警告（不泄露 key），流程不中断。

## 目录结构

见 `SPEC.md` 第"目录结构"一节（契约，不得擅自更改）。
