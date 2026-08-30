# SPEC.md — 多Agent学习规划协作系统（骨架版）

课程《大模型与智能体工程实践》大作业 · 方案二骨架。
目标：**离线可跑通**（默认 Mock LLM + 模拟数据，无需任何 API Key），
各模块预留扩展点，方便组员后续接入真实 LLM/API。

## 技术栈
- Python 3.10+，仅标准库 + streamlit（前端）
- 不使用 LangChain 等重型框架（课程作业，展示原理为主）
- 所有 Agent 默认使用 `MockLLM`（规则模板生成），真实 LLM 通过统一接口可插拔

## 目录结构（契约，不得擅自更改）
```
project/
├── README.md               # 运行说明 + 四人分工扩展点
├── requirements.txt        # streamlit>=1.30
├── context_store.py        # ContextStore：共享上下文读写
├── context_store.json      # 初始模板（运行时会被覆盖）
├── tool_schemas.json       # MCP 工具 Schema 定义
├── mcp_server.py           # MCPServer：统一工具接口（模拟数据）
├── harness.py              # Harness：敏感操作审批队列
├── orchestrator.py         # Orchestrator：状态机调度 + CRISP 上下文筛选
├── demo.py                 # 命令行全流程演示（无 streamlit 也能跑）
├── app.py                  # Streamlit 前端
├── agents/
│   ├── __init__.py
│   ├── base_agent.py       # BaseAgent + LLMClient 协议 + MockLLM
│   ├── diagnose_agent.py   # 诊断Agent：RAG检索错题 → 诊断报告
│   ├── planner_agent.py    # 规划Agent：查日历/天气 → 一周计划
│   └── tutor_agent.py      # 辅导Agent：加载Skill → 苏格拉底式引导
├── skills/
│   └── skill_tutor.md      # 苏格拉底教学 SOP（参考课程PPT p88-90）
└── data/
    ├── mock_calendar.json  # 模拟日历
    ├── mock_weather.json   # 模拟天气
    └── mock_rag_corpus.json# 模拟错题本/教材语料
```

## 核心接口契约

### 1. ContextStore（context_store.py）
```python
class ContextStore:
    def __init__(self, path: str = "context_store.json") -> None
    def load(self) -> dict                      # 从JSON读入内存并返回
    def save(self) -> None                      # 写回JSON（indent=2, ensure_ascii=False）
    def reset(self, session_id: str, user_profile: dict) -> None  # 初始化新会话
    def get(self, key: str, default=None)       # 读顶层字段
    def start_task(self, agent: str, input_note: str = "") -> None   # task_chain追加/更新为 running
    def finish_task(self, agent: str, output: str, extra: dict | None = None) -> None  # 标记 done + 记录输出
    def set_pending_approval(self, approval: dict | None) -> None
    def snapshot(self) -> dict                  # 返回当前内存state的深拷贝
```
`context_store.json` 初始模板：
```json
{
  "session_id": "",
  "user_profile": {},
  "task_chain": [],
  "pending_approval": null
}
```

### 2. MCPServer（mcp_server.py）
```python
class MCPServer:
    def __init__(self, schemas_path: str = "tool_schemas.json", data_dir: str = "data") -> None
    def list_tools(self) -> list[dict]          # 返回 tool_schemas.json 内容
    def call_tool(self, name: str, args: dict) -> dict  # 统一调用入口，返回 {"ok": bool, "result": ..., "error": str|None}
```
四个工具（全部返回模拟数据，从 data/ 读取；找不到时返回内置兜底值）：
| name | args | 说明 | sensitive |
|---|---|---|---|
| rag_search | query:str, top_k:int=3 | 错题本/教材检索（简单关键词匹配打分） | 否 |
| check_calendar | date:str | 查某日安排 | 否 |
| check_weather | city:str, date:str | 查天气 | 否 |
| send_email | to:str, subject:str, body:str | 发邮件（骨架中只打印+返回模拟回执） | **是** |

`tool_schemas.json`：list，每项 `{name, description, parameters(JSON Schema), sensitive:bool}`。

### 3. Harness（harness.py）
```python
SENSITIVE_TOOLS = {"send_email"}  # 与schemas中sensitive=true保持一致
class Harness:
    def __init__(self, store: ContextStore) -> None
    def is_sensitive(self, tool_name: str) -> bool
    def request_approval(self, agent: str, tool_name: str, args: dict) -> dict
        # 生成 approval 记录 {"id","agent","tool","args","reason","status":"pending"}
        # 写入 store.set_pending_approval(...) 并返回该记录
    def approve(self) -> dict     # 批准当前pending：status→approved，清空pending，返回记录
    def reject(self, reason: str = "") -> dict  # 拒绝：status→rejected，清空pending
```

### 4. BaseAgent + LLM 抽象（agents/base_agent.py）
```python
class LLMClient(Protocol):
    def chat(self, system: str, user: str) -> str: ...

class MockLLM:                     # 默认离线实现：基于关键词的规则模板回复
    def chat(self, system: str, user: str) -> str

class BaseAgent:
    name: str
    def __init__(self, mcp: MCPServer, store: ContextStore, harness: Harness, llm: LLMClient | None = None)
        # llm=None 时用 MockLLM
    def run(self, context: dict) -> dict   # 子类实现；返回 {"output": str, ...}
    def call_tool(self, name: str, args: dict) -> dict
        # 统一走 harness 检查：敏感工具 → harness.request_approval 并返回 {"ok":False,"pending_approval":...}
```

### 5. 三个 Agent（agents/*.py）
- **DiagnoseAgent(name="诊断Agent")**：调 `rag_search` 检索错题 → MockLLM 生成诊断报告（指出薄弱点），返回 `{"output": str, "rag_sources": list[str]}`。
- **PlannerAgent(name="规划Agent")**：调 `check_calendar` + `check_weather`（一周7天）→ 结合诊断结果生成一周学习计划表（Markdown表格），返回 `{"output": str, "tool_calls": list[str]}`。
- **TutorAgent(name="辅导Agent")**：读取 `skills/skill_tutor.md` 内容放入 system prompt → 针对诊断出的薄弱点生成苏格拉底式引导提问（不直接给答案），返回 `{"output": str, "skill_used": "skill_tutor.md"}`。

### 6. Orchestrator（orchestrator.py）
```python
TASK_ORDER = ["诊断Agent", "规划Agent", "辅导Agent"]
class Orchestrator:
    def __init__(self, agents: dict[str, BaseAgent], store: ContextStore) -> None
    def select_context(self, agent_name: str) -> dict
        # CRISP体现：诊断Agent只给user_profile；规划Agent给user_profile+诊断output；
        # 辅导Agent给诊断output+计划output（不塞全量task_chain）
    def run_session(self, session_id: str, user_profile: dict) -> dict
        # store.reset → 按TASK_ORDER依次 start_task/run/finish_task → 返回最终snapshot
        # 每个Agent运行中若触发 pending_approval，暂停并在结果中标注
```

### 7. skills/skill_tutor.md
参考课程PPT p88-90 的苏格拉底教学SOP：YAML元数据头（name/description）+
教学原则（永不直接给答案、苏格拉底提问、连续错2次降难度、每次只讲一个概念）+
工作流程 + 禁忌行为。

### 8. demo.py
`python demo.py` 离线跑通全流程：构造示例 user_profile（英语薄弱/网球游泳/周三晚上效率低），
Orchestrator 跑三个Agent，终端逐步打印每个Agent的状态流转与输出；
最后演示 Harness：TutorAgent 尝试 send_email → 进入审批 → 自动 approve → 打印回执。

### 9. app.py（Streamlit）
- 侧边栏/表单输入学生信息（薄弱学科、运动、备注）→ 点击"开始"跑全流程
- 主界面分栏展示：Context Store 实时JSON（st.json）+ 各Agent输出（st.expander）
- pending_approval 非空时显示审批卡片 + 【批准】【拒绝】按钮
- 页面顶部一句话架构图说明

### 10. README.md
- 快速开始：`pip install -r requirements.txt` → `python demo.py` / `streamlit run app.py`
- 架构图（ASCII，来自分工文档）+ 课程术语落点表（MCP/Tool/RAG/Memory/Context Engineering/Skill/Harness/CRISP → 对应文件）
- **四人分工扩展点**：同学A→mcp_server.py接真实API；同学B→orchestrator.py调度策略；同学C→agents与skill_tutor.md；同学D→app.py与harness.py、Memory持久化

## 质量要求
- 全部中文注释/文档字符串；代码风格清晰，适合大一学生阅读扩展
- `python demo.py` 必须在无网络、无API Key环境下0报错跑通
- 不引入 streamlit 之外的第三方依赖
