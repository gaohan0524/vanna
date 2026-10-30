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

旧入口 `src/vanna/examples/web_manual_test.py` 已删除，不再保留兼容 shim。

## 2. 与旧裸 Agent 的区别

旧页面是裸 Agent：

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
| `analytics_agent/web/index.html` | 业务侧首页模板，带 Plotly |
| `load_index_html` | 读取业务首页，不污染框架默认页 |
| `ChatHandler` + SSE | `/api/vanna/v2/chat_sse` |
| `/health` | 健康检查 |

### 3.1 为什么首页放在业务目录

首页放在：

```text
analytics_agent/web/index.html
```

不放 `src/vanna/servers/base/templates.py`，也不替换框架默认页。

原因：

- 这是 **Analytics Agent 的业务 UI**，不是 Vanna 框架的通用默认页；
- 它依赖 Plotly CDN、中文标题和本项目的 SSE 结构；
- 放进框架会扩大改动面，影响其他示例和用户；
- 放在业务目录可以独立改 HTML/JS，不需要动 Python 装配逻辑；
- `web_app.py` 通过 `load_index_html()` 读取，路径清晰、易于交付。

如果以后多个业务 Agent 共用同一套前端，再抽成框架组件；当前不做提前抽象。

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

- `AGENT_MAX_ITERATIONS` 统一默认 **8**，业务层只在 `analytics_agent/config.py` 定义；
- 需要调整时只改 `.env` 的 `AGENT_MAX_ITERATIONS`，不要在各入口重复写默认值；
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
