# Analytics Agent 交付 Web 入口

## 1. 入口

```text
analytics_agent/web_app.py
```

运行：

```bash
python -m analytics_agent.web_app
```

浏览器打开：

```text
http://localhost:8000
```

旧的兼容入口仍可用，但已改为薄包装：

```bash
python src/vanna/examples/web_manual_test.py
python -m vanna.examples.web_manual_test
```

新代码统一使用 `analytics_agent.web_app`。

## 2. 与旧 Web 测试页的区别

旧 `web_manual_test.py` 是裸 Agent：

```text
run_sql + visualize_data
无 schema
无 glossary
无 AnalyticsPromptBuilder
无 Stage 2 护栏
```

新交付入口走统一工厂：

```text
Settings
  → analytics_agent.agent_factory.build_agent()
    → AnalyticsPromptBuilder（schema + glossary）
    → 7 个 Stage 2 工具
    → SQL 护栏
    → AgentMemory（默认 demo，可切 chroma_local）
```

因此 Web 和评测 CLI 使用同一套 Agent 核心，不再出现“Web 裸跑”和“评测 Agent”两套实现。

## 3. 组件

| 组件 | 说明 |
|---|---|
| `build_agent` | 复用评测/线上同一装配逻辑 |
| `WebUserResolver` | 从 `vanna_email` cookie 解析用户 |
| `BarFirstChartGenerator` | 多列结果优先出柱状图 |
| `INDEX_HTML` | 带 Plotly 的对话页面 |
| `ChatHandler` + SSE | `/api/vanna/v2/chat_sse` |
| `/health` | 健康检查 |

## 4. 用户与权限

默认：

```text
无 cookie → analyst@example.com → analyst 组
admin@*   → admin 组
```

管理员通过以下方式显式配置：

```text
WEB_ADMIN_EMAILS=admin@example.com,ops@example.com
```

也可以设置浏览器 cookie：

```text
vanna_email=admin@example.com
```

注意：

- 这是内网/演示用 cookie 身份，不是生产认证；
- 不把“无 cookie”默认成 admin；
- 生产部署应替换 `WebUserResolver`，接 SSO/JWT/网关身份。

## 5. 配置

`.env`：

```text
# Web 服务
WEB_HOST=0.0.0.0
WEB_PORT=8000
WEB_DEFAULT_EMAIL=analyst@example.com
WEB_ADMIN_EMAILS=admin@example.com

# Agent 本身仍使用原来的配置
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
CH_HOST=localhost
CH_PORT=8123
CH_DATABASE=shop
CH_USER=readonly
CH_PASSWORD=...
AGENT_MAX_ITERATIONS=8
```

注意：

- `AGENT_MAX_ITERATIONS` 由 Agent 配置读取，不再由 Web 页单独设成 16；
- Stage 3 记忆默认 `MEMORY_BACKEND=demo`；要接 Chroma 才设置 `chroma_local`；
- 种子未导入时，即使开 Chroma 也检索不到知识。

## 6. 运行时行为

```text
浏览器消息
  → /api/vanna/v2/chat_sse
  → ChatHandler
  → build_agent 创建的 Agent
  → schema/glossary prompt
  → 工具循环
  → SSE 返回 text / dataframe / chart / status
```

## 7. 冒烟验证

不启动服务，先验证模块可导入：

```bash
python -c "import analytics_agent.web_app; print('ok')"
```

用户解析测试：

```bash
pytest tests/test_web_app.py -q
```

启动前确认：

```bash
python -m analytics_agent.evaluation.cli check --strict \
  --dataset analytics_agent/evaluation/datasets/v1_30.yaml
```

## 8. 与评测的关系

- Web 入口不是新的评测入口；
- 30 条基线仍用 `analytics_agent/evaluation/cli.py`；
- Web 入口用于人工验收、演示和排查真实对话体验；
- 两者共用 `build_agent`，避免装配漂移。
