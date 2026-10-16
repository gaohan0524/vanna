# CODEBUDDY.md 本文件为 CodeBuddy 在本仓库中工作时提供指引。

## 常用命令

**安装 / 开发环境搭建** —— 以可编辑模式安装，包含所有供应商 extras 与开发工具。
```bash
pip install -e ".[all]"
pip install tox ruff mypy pytest pytest-asyncio
```

**运行单元测试** —— 无外部依赖的测试集（`test_tool_permissions`、`test_llm_context_enhancer`、`test_workflow`、`test_memory_tools`）。
```bash
tox -e py311-unit
```

**运行单个测试** —— pytest 配置位于 `pyproject.toml`（`asyncio_mode = "auto"`，`testpaths = ["tests"]`）。
```bash
pytest tests/test_tool_permissions.py -v
pytest tests/test_database_sanity.py::TestSqliteRunner -v
```

**运行集成 / 供应商测试** —— 每个供应商或数据库对应一个 tox 环境；缺少 API Key 时 `tests/conftest.py` 会自动跳过。
```bash
tox -e py311-anthropic   # 需要 ANTHROPIC_API_KEY
tox -e py311-sqlite-sanity
tox -e py311-legacy      # LegacyVannaAdapter
```

**代码检查 / 格式化** —— ruff 配置位于 `pyproject.toml`（行宽 88，双引号）。
```bash
ruff format src/vanna/ tests/     # 应用格式化
ruff check src/vanna/ tests/      # 静态检查
tox -e ruff                       # format --check + check
```

**类型检查** —— mypy strict 模式，仅覆盖 `tools/ core/ capabilities/ agents/ utils/ web_components/ components/`。
```bash
tox -e mypy
```

**启动演示服务** —— CLI 入口为 `vanna`（`vanna.servers.cli.server_runner:main`），默认使用 FastAPI 监听 :8000。
```bash
vanna --list-examples
vanna --example mock_quickstart --dev   # --dev 会托管 frontends/webcomponent/static
```

**前端** —— `frontends/webcomponent/` 中基于 Lit + Vite 的 Web 组件（`<vanna-chat>`）。
```bash
cd frontends/webcomponent && npm install && npm run dev    # 或：npm run build
```

## 架构总览

Vanna 2.0 是一个**面向用户身份的 LLM 智能体框架**：把自然语言转换为 SQL、表格、图表和总结，并以结构化 UI 组件的形式流式返回。仓库中存在两个并存的世界：

- **新框架**（`src/vanna/{core,tools,capabilities,components,integrations,servers,examples}`）——日常开发应修改的部分。
- **冻结的 1.x 代码**（`src/vanna/legacy/`）——旧的 `VannaBase` 单体类（约 30 个供应商子包）。视为只读，只做适配、不要重写。

### 请求主流程（核心骨架）

`Agent` 之下的所有逻辑都是同一个 `AsyncGenerator[UiComponent, None]`；Web 层只负责重新编码，服务端不保存任何组件状态。

```
HTTP POST /api/vanna/v2/chat_sse
  → routes.py 构造 RequestContext{cookies, headers, remote_addr, query_params, metadata}
  → ChatHandler.handle_stream()                     # servers/base/chat_handler.py
  → Agent.send_message() → Agent._send_message()    # core/agent/agent.py
      1. user_resolver.resolve_user(request_context)   → User(id, email, group_memberships)
      2. workflow_handler.get_starter_ui()             （空消息 → 渲染初始界面）
      3. lifecycle_hooks.before_message()              （可改写用户消息）
      4. conversation_store.get_conversation()
      5. workflow_handler.try_handle()                 （短路处理：/help、/status、/memories）
      6. 构造 ToolContext(user, conversation_id, request_id, agent_memory, …)
      7. context_enrichers → tool_registry.get_schemas(user) → system_prompt_builder
         → llm_context_enhancer.enhance_system_prompt()
      8. 循环（max_tool_iterations，默认 10）：
           llm_service.stream_request() → 若有工具调用：逐个 tool_registry.execute()，
           追加 role="tool" 消息，重建请求，继续循环；否则输出最终答案
      9. lifecycle_hooks.after_message()，conversation_store.update_conversation()
  → ChatStreamChunk.from_component() → SSE `data: {json}\n\n`，以 `data: [DONE]\n\n` 结束
```

`Agent.send_message` 用统一的 try/except 包裹 `_send_message`：出错时输出 `StatusCardComponent` 错误卡片并重新启用输入框，因此单个组件失败不会中断整个流。

### 核心抽象（`src/vanna/core/`）

`core/` 按关注点划分为多个小包，每个包包含 `base.py`（抽象基类），通常还有 `default.py`。`core/__init__.py` 统一再导出公共接口，`vanna/__init__.py` 再次导出——**新增公共名称时两处都要添加**。

- **`agent/agent.py`** —— `Agent` 编排器。`AgentConfig`（`core/agent/config.py`）包含 `max_tool_iterations`、`stream_responses`、`temperature`、`ui_features`、`audit_config`。
- **`tool/base.py`** —— `Tool[T]` 抽象基类：`name`、`description`、`access_groups`（默认 `[]`，即所有人可用）、`get_args_schema()`（Pydantic 模型）、`async execute(context, args) -> ToolResult`。
- **`registry.py`** —— `ToolRegistry`。提供 `register_local_tool(tool, access_groups)`、`get_schemas(user)`、`execute(tool_call, context)`。权限基于 `User.group_memberships` 与 `Tool.access_groups` 的**分组交集**。`transform_args()` 是做行级安全（RLS）/ 按用户改写参数的钩子（默认空实现，返回 `ToolRejection` 即拒绝执行）。
- **`user/`** —— `User`、`RequestContext`、`UserResolver`（由你实现从 cookie/JWT/header 中提取身份）、`UserService`。
- **`llm/`** —— `LlmService` 抽象基类（`send_request`、`stream_request`、`validate_tools`）以及 `LlmRequest/LlmResponse/LlmStreamChunk/LlmMessage/ToolCall`。
- **`storage/`** —— `ConversationStore`（`Conversation`、`Message`）；默认实现是 `integrations/local` 中的 `MemoryConversationStore`。

### 扩展点（均为可选，除 `error_recovery_strategy` 外都真实生效）

`Agent.__init__` 接收这些扩展点的列表；应通过它们扩展行为，而不是继承 `Agent` 或修改主循环：

- `lifecycle_hooks` —— `before_message`、`after_message`、`before_tool`、`after_tool`（配额、日志、改写结果）。
- `llm_middlewares` —— `before_llm_request`、`after_llm_response`（缓存、成本统计、内容过滤）。
- `context_enrichers` —— 在工具执行前往 `ToolContext.metadata` 注入数据。
- `llm_context_enhancer` —— RAG 式的提示词 / 消息注入；`DefaultLlmContextEnhancer` 从 `AgentMemory` 中检索文本记忆。
- `conversation_filters` —— 每次调用 LLM 前裁剪 / 脱敏历史消息。
- `system_prompt_builder` —— 默认自动注入日期、工具列表和记忆说明块。
- `workflow_handler` —— `DefaultWorkflowHandler` 实现 `/help`、`/status`（仅管理员）、`/memories`，以及根据配置生成的初始界面。
- `observability_provider` —— span / 指标；几乎所有步骤都已被埋点。
- `audit_logger` —— `log_tool_access_check/invocation/result/ui_feature_access/ai_response`，带参数脱敏；由 `AgentConfig.audit_config` 控制，并由 `Agent` 注入到 `ToolRegistry`。
- `error_recovery_strategy` —— 已声明但**当前未在 `agent.py` 中调用**。

### 能力层与工具

`capabilities/` 存放可替换后端的抽象基类：`SqlRunner.run_sql(args, context) -> pd.DataFrame`、`AgentMemory`（存储 `ToolMemory`（问题/工具/参数）与 `TextMemory`，提供检索/保存/删除接口）、`FileSystem`。

`src/vanna/tools/` 内置工具：
- `run_sql.py` —— `RunSqlTool`，注入 `SqlRunner`；返回 `DataFrameComponent`，同时通过 `FileSystem` 写出 `query_results_<hex>.csv`（这正是仓库根目录出现 `258d8dc916db8cea/` 目录的原因——按用户目录 = `sha256(user.id)[:16]`）。
- `visualize_data.py` —— 用 pandas 读取该 CSV 并返回 Plotly `ChartComponent`。
- `agent_memory.py` —— `save_question_tool_args`、`search_saved_correct_tool_uses`、`save_text_memory`（直接从 `vanna.tools.agent_memory` 导入，未在包 `__init__` 中再导出）。
- `file_system.py`、`python.py` —— 文件操作与脚本执行工具（供 coding-agent 示例使用）。

### 组件（`src/vanna/components/`）

`UiComponent`（Pydantic 模型，定义在 `core/components.py`）同时携带**两种表示**，使同一份数据既能在富客户端也能在简单客户端渲染：

- `RichComponent`（`core/rich_component.py`，子类位于 `components/rich/`）—— 包含 id、`ComponentType`、`ComponentLifecycle`（create/update/replace/remove）、children、可见性等。序列化必须走 **`serialize_for_frontend()`**，而非直接 `model_dump()`：它会把非标准字段收拢到 `payload["data"]` 下并把枚举转成字符串。这是与前端的传输契约——新增组件时务必遵守。
- `SimpleComponent`（`core/simple_component.py`）—— 仅支持文本 / 图片 / 链接。

工具名、参数、错误、记忆详情的可见性由 `UiFeature` 与 `UiFeatures.can_user_access_feature` 控制（默认仅管理员可见 SQL）。

### 集成层（`src/vanna/integrations/`）

每个供应商一个目录，结构统一：`__init__.py` 从按能力命名的模块中导出单个类——`llm.py`（`AnthropicLlmService`、`OpenAILlmService` 等）、`sql_runner.py`（`SqliteRunner`、`PostgresRunner` 等）、`agent_memory.py`（`ChromaAgentMemory` 等）、`chart_generator.py`。较重的依赖都作为 `pyproject.toml` 中的可选 extras，各集成在内部按需导入 SDK。`integrations/local/` 是无依赖的基础实现（`MemoryConversationStore`、`LocalFileSystem`、`DemoAgentMemory`、`LoggingAuditLogger`），`integrations/mock/` 提供供测试和示例使用的 `MockLlmService`。

### 服务层（`src/vanna/servers/`）与前端

`base/` 是与框架无关的部分（`ChatHandler`、`ChatRequest/ChatStreamChunk/ChatResponse`，以及 `templates.py` 提供的 Tailwind 风格首页，用于挂载 Web 组件包）。`fastapi/` 是默认实现并提供真正的 WebSocket 端点；`flask/` 与之对应，但 WebSocket 返回 501。两者都暴露 `GET /`、`POST /api/vanna/v2/chat_sse`、`POST /api/vanna/v2/chat_poll` 与 `/health`。dev 模式托管 `frontends/webcomponent/static`，否则组件包从 CDN 加载（`https://img.vanna.ai/vanna-components.js`）。

注意：`servers/base/rich_chat_handler.py` 与 `ComponentManager` 的树差异更新协议目前是已注释的死代码。

### 评估框架（`src/vanna/core/evaluation/`）

由 `TestCase` + `ExpectedOutcome` + `Evaluator` 抽象基类构成，内置 `TrajectoryEvaluator`、`OutputEvaluator`、`LLMAsJudgeEvaluator`、`EfficiencyEvaluator`；`EvaluationRunner` 支持并发运行与对比（`compare_agents`、`compare_agents_streaming`），输出 `EvaluationReport`/`ComparisonReport`（`save_html`）。数据集可从 YAML/JSON 加载。

### 旧版兼容桥接

`LegacyVannaAdapter`（`src/vanna/legacy/adapter.py`）同时继承 **`ToolRegistry`** 与 **`AgentMemory`**，包装 1.x 的 `vn` 对象（`LegacySqlRunner` 适配同步的 `run_sql`，`add_question_sql` 映射到 `save_tool_usage` 等）。`src/vanna/legacy/__init__.py` 是弃用垫片：旧顶层函数会抛错并引导到 `vanna.remote.VannaDefault`。

### 代码约定

- 使用 ruff（行宽 88）；对 `tox.ini` 列出的目录执行 mypy strict；新代码需补充类型标注。
- 用 `TYPE_CHECKING` 规避循环导入；`core/__init__.py` 在导入完成后对 `ToolContext`/`ToolResult` 调用 `model_rebuild()`。
- 单元测试不得依赖外部服务——请 mock `LlmService`，并为供应商相关测试加标记（如 `@pytest.mark.anthropic`）。
- 示例位于 `src/vanna/examples/`，由 CLI 通过 `create_demo_agent` / `create_agent` / `create_basic_demo` / `main_agent` 这些工厂名称加载。
