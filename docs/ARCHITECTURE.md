# Vanna 2.0 架构图

> 依据 `src/vanna` 当前代码结构绘制。Mermaid 图可在 CodeBuddy / VS Code 的 Markdown 预览、GitHub 上直接渲染。

## 0. 一张图看懂

**产品做什么**：用户用自然语言提问，Vanna 自动完成「查库 → 算数 → 画图 → 总结」，把结果以表格、图表、文本卡片等 UI 组件流式返回到聊天界面。

**怎么实现**：把一次提问拆成三步——装配上下文、让大模型决定调哪个工具、把工具产出的组件流式推给前端；三者在一个循环里反复迭代，直到模型给出最终答案。

```mermaid
flowchart TB
%%{init: {'theme': 'base', 'themeVariables': { 'clusterBkg': '#FFF8DC', 'clusterBorder': '#FFA500', 'clusterBorderWidth': '4px' }}}%%
    IN["用户输入<br/>问业务问题"] -->|用户请求| FRONTEND[前端界面]
	FRONTEND[前端界面] --> CTX
    subgraph ORCH["智能体编排：Agent 主循环"]
        CTX["1. 装配上下文<br/>身份权限 + 对话历史<br/>+ 业务元数据 + 历史记忆"]
        LLM["2. 大模型决策<br/>直接回答，还是调用工具"]
        Q{"需要工具?"}
        TOOL["3. 工具执行<br/>查库 SQL · 画图<br/>· 读写记忆 · 读文件"]
        CTX --> LLM
        LLM --> Q
        Q -->|需要| TOOL
        TOOL --> LLM
    end

    Q -->|不需要| OUT["给出结果<br/>表格 / 图表 / 文本"]
    OUT -->|前端流式渲染SSE增量组件,边算边显示| FRONTEND
    FRONTEND -->|展示给用户| IN
```



一句话：**自然语言进，结构化分析结果出**；编排靠 `Agent` 主循环，能力靠可插拔的 `Tool` + 供应商集成（大模型、数据库、向量库），展示靠统一的 `UiComponent` 流。

## 1. 分层总览

```mermaid
flowchart TB
    subgraph CLIENT["客户端层"]
        WC["frontends/webcomponent<br/>Lit + Vite · vanna-chat 组件"]
        OTHER["其它客户端<br/>curl / 自定义前端"]
    end

    subgraph SERVER["服务层 src/vanna/servers"]
        FA["servers/fastapi<br/>/chat_sse · /chat_poll · WebSocket"]
        FL["servers/flask<br/>WebSocket 返回 501"]
        BASE["servers/base<br/>ChatHandler · ChatStreamChunk · templates"]
        CLI["servers/cli<br/>vanna 命令行入口"]
    end

    subgraph AGENT["编排层 core/agent"]
        AG["Agent.send_message<br/>_send_message 主循环"]
        CFG["AgentConfig<br/>max_tool_iterations / ui_features / audit_config"]
    end

    subgraph EXT["扩展点（Agent 构造参数注入）"]
        H["lifecycle_hooks"]
        M["llm_middlewares"]
        CE["context_enrichers"]
        LC["llm_context_enhancer"]
        CF["conversation_filters"]
        SP["system_prompt_builder"]
        WF["workflow_handler"]
        OB["observability_provider"]
        AU["audit_logger"]
    end

    subgraph CORE["核心抽象 core/"]
        TR["ToolRegistry<br/>get_schemas · execute · transform_args"]
        LLM["LlmService<br/>send_request · stream_request"]
        ST["ConversationStore"]
        USR["UserResolver / UserService"]
        CMP["UiComponent<br/>RichComponent · SimpleComponent"]
    end

    subgraph CAP["能力层 capabilities/"]
        SQL["SqlRunner"]
        MEM["AgentMemory"]
        FS["FileSystem"]
    end

    subgraph TOOLS["工具层 tools/"]
        T1["RunSqlTool"]
        T2["VisualizeDataTool"]
        T3["agent_memory 工具集"]
        T4["file_system / python"]
    end

    subgraph INTEG["集成层 integrations/"]
        I1["LLM 供应商<br/>openai · anthropic · ollama · deepseek ..."]
        I2["数据库<br/>sqlite · postgres · snowflake · bigquery ..."]
        I3["向量库 / 记忆<br/>chromadb · faiss · qdrant ..."]
        I4["local（无依赖）<br/>MemoryConversationStore · LocalFileSystem"]
        I5["mock（测试）<br/>MockLlmService"]
        I6["可观测<br/>otel · langfuse · langsmith"]
    end

    WC --> FA
    OTHER --> FA
    OTHER --> FL
    FA --> BASE
    FL --> BASE
    BASE --> AG
    CLI --> AG
    CFG -.-> AG
    EXT --> AG
    AG --> CORE
    AG --> CAP
    TR --> TOOLS
    TOOLS --> CAP
    CORE --> INTEG
    CAP --> INTEG
    CMP --> BASE
```

## 2. 一次对话的请求时序

```mermaid
sequenceDiagram
    autonumber
    participant B as 浏览器 / 客户端
    participant R as fastapi/routes.py
    participant CH as ChatHandler
    participant AG as Agent
    participant UR as UserResolver
    participant WF as WorkflowHandler
    participant CS as ConversationStore
    participant TR as ToolRegistry
    participant LS as LlmService
    participant UI as UiComponent

    B->>R: POST /api/vanna/v2/chat_sse
    R->>R: 构造 RequestContext<br/>(cookies/headers/remote_addr/query_params/metadata)
    R->>CH: handle_stream()
    CH->>AG: send_message()
    AG->>UR: resolve_user(request_context)
    UR-->>AG: User(id, email, group_memberships)
    AG->>WF: get_starter_ui() / try_handle()
    Note over WF: /help · /status（管理员）· /memories<br/>命中则短路，不调用 LLM
    AG->>CS: get_conversation()
    CS-->>AG: 历史消息

    loop max_tool_iterations（默认 10）
        AG->>TR: get_schemas(user)
        TR-->>AG: 该用户可见的工具 schema
        AG->>AG: context_enrichers / system_prompt_builder<br/>llm_context_enhancer
        AG->>LS: stream_request()
        LS-->>AG: 文本增量 / ToolCall
        alt 返回工具调用
            AG->>TR: execute(tool_call, ToolContext)
            TR->>TR: transform_args（行级安全 / 拒绝）
            TR-->>AG: ToolResult（UiComponent 列表）
            AG->>UI: yield 组件（create/update/replace/remove）
            AG->>LS: 追加 role="tool" 后继续循环
        else 返回最终答案
            AG->>UI: yield 文本组件
        end
    end

    AG->>AG: after_message / update_conversation
    AG-->>CH: AsyncGenerator[UiComponent]
    CH->>CH: ChatStreamChunk.from_component()
    CH-->>B: SSE data:{json} ... data:[DONE]
```

## 3. 工具调用与权限控制

```mermaid
flowchart LR
    Q["用户消息"] --> P["组装 system prompt<br/>+ 上下文增强"]
    P --> L["LlmService.stream_request"]
    L --> D{"有 ToolCall?"}
    D -->|否| OUT["yield 最终文本组件"]
    D -->|是| V{"工具在<br/>user 可见 schema 中?"}
    V -->|否| REJ["拒绝：返回错误结果给 LLM"]
    V -->|是| TA["transform_args<br/>RLS / 参数改写"]
    TA --> EX["tool.execute(context, args)"]
    EX --> RES["ToolResult<br/>UiComponent[] + 可选后续动作"]
    RES --> LOOP["追加 role=tool 消息"]
    REJ --> LOOP
    LOOP --> L
    OUT --> DONE["结束流"]

    subgraph PERM["权限模型"]
        G1["Tool.access_groups（默认空 = 全员可用）"]
        G2["User.group_memberships"]
        G3["交集非空才允许执行"]
        G1 --> G3
        G2 --> G3
    end
```

## 4. 组件渲染链路

```mermaid
flowchart LR
    TOOL["工具产出<br/>DataFrameComponent<br/>ChartComponent<br/>StatusCardComponent"] --> RICH["RichComponent<br/>ComponentType · ComponentLifecycle · children"]
    TOOL --> SIM["SimpleComponent<br/>text / image / link"]
    RICH --> SER["serialize_for_frontend()<br/>非标准字段收拢到 payload.data，枚举转字符串"]
    SIM --> SER
    SER --> CHUNK["ChatStreamChunk"]
    CHUNK --> SSE["SSE 流式推送<br/>服务端不保存组件树"]
    SSE --> FRONT["前端按 lifecycle 自行 diff 渲染"]
```

## 5. 模块职责速查

| 目录 | 职责 |
| --- | --- |
| `core/agent/` | `Agent` 编排器与 `AgentConfig` |
| `core/tool/` `core/registry.py` | `Tool` 抽象、`ToolRegistry`、权限与 `transform_args` |
| `core/llm/` | `LlmService` 抽象与请求/响应模型 |
| `core/user/` | `User`、`RequestContext`、`UserResolver`、`UserService` |
| `core/storage/` | `ConversationStore` 抽象（默认实现见 `integrations/local`） |
| `core/` 下的扩展点子包 | `lifecycle`、`middleware`、`enricher`、`enhancer`、`filter`、`workflow`、`observability`、`audit`、`system_prompt` 的抽象与默认实现 |
| `core/evaluation/` | 评估框架：`TestCase`、`Evaluator`、`EvaluationRunner`、报告导出 |
| `core/components.py` `rich_component.py` `simple_component.py` | `UiComponent` 双表示传输契约 |
| `capabilities/` | `SqlRunner`、`AgentMemory`、`FileSystem` 可替换后端 |
| `tools/` | 内置工具：`run_sql`、`visualize_data`、`agent_memory`、`file_system`、`python` |
| `integrations/` | 各供应商实现；`local/` 无依赖基础实现，`mock/` 供测试 |
| `servers/` | `base` 框架无关层、`fastapi`（默认）、`flask`、`cli` |
| `legacy/` | 冻结的 1.x 实现与 `LegacyVannaAdapter` 桥接（只读） |
| `frontends/webcomponent/` | Lit + Vite 的 `vanna-chat` Web 组件 |

## 6. 要点

- 全链路只有一个数据类型：`AsyncGenerator[UiComponent, None]`；Web 层仅做编码转换，服务端无组件状态。
- 扩展优先，不改主循环：通过 `Agent.__init__` 的扩展点列表注入行为（`error_recovery_strategy` 目前已声明但未在 `agent.py` 中调用）。
- 权限基于「分组交集」，默认 `Tool.access_groups = []` 表示全员可用；SQL 等敏感信息的可见性由 `UiFeature` 控制（默认仅管理员）。
- 新增公共名称需同时更新 `core/__init__.py` 与 `vanna/__init__.py`。
