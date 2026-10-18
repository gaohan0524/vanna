# P1-11 可观测性对接标准（OTel / Langfuse / LangSmith）

- **对应问题**：#11 observability 是自建抽象，未接标准
- **提交**：`23e0aa7 feat: 可观测性对接标准（OTel / Langfuse / LangSmith + composite）`
- **优先级**：P1（功能/体验差距）
- **状态**：已完成

---

## 1. 问题背景

优化前，`ObservabilityProvider` 只有自建抽象（`Span` / `Metric`），没有内置的标准后端实现：

- 接入 OpenTelemetry / Langfuse / LangSmith 需要用户自己写适配器；
- 排障、评测、成本分析难以复用现有平台；
- 无法把同一份遥测数据同时送到多个后端。

主流做法是：

- **以 OpenTelemetry 作为厂商中立的通用标准**（OTLP 可导出到 Jaeger、Tempo、Grafana、Datadog、Langfuse OTLP 等）；
- 同时为 **LLM 专用平台（Langfuse / LangSmith）提供原生 SDK 适配**；
- 支持 **composite**，把数据扇出到多个后端。

---

## 2. 主流方案参考

| 标准/平台 | 定位 | 本项目支持 |
|---|---|---|
| OpenTelemetry (OTel) | 通用 traces/metrics/logs 标准，OTLP 导出 | `OpenTelemetryObservabilityProvider` |
| Langfuse | LLM 专用（trace/span/generation/score），也支持 OTLP | `LangfuseObservabilityProvider` |
| LangSmith | LangChain 生态（RunTree） | `LangSmithObservabilityProvider` |
| 标准库 logging | 本地零依赖排障 | `LoggingObservabilityProvider` |
| 多后端并行 | 同时上报 | `CompositeObservabilityProvider` |

---

## 3. 方案设计

### 3.1 统一接口不变

现有 `ObservabilityProvider` 接口保持不变：

```python
async def record_metric(name, value, unit="", tags=None) -> None
async def create_span(name, attributes=None) -> Span
async def end_span(span) -> None
```

所有新 provider 都实现这三个方法，因此可以无缝替换 `Agent(observability_provider=...)`。

### 3.2 OpenTelemetry provider

- 支持传入现成 `tracer` / `meter`（推荐：应用统一配置 OTel SDK 与 exporter）；
- 或传 `endpoint`，自动装配 OTLP HTTP/gRPC exporter；
- `create_span` → `tracer.start_span(...)`；`end_span` → 同步属性 + `duration_ms` + `span.end()`；
- `record_metric` → counter（`unit="count"`）或 histogram（其它）；
- 非原生类型属性自动 JSON 序列化；
- 提供 `force_flush()`。

```python
provider = OpenTelemetryObservabilityProvider(
    service_name="vanna",
    endpoint="http://localhost:4318",   # 可选，自动装配 OTLP HTTP exporter
)
```

### 3.3 Langfuse provider

- span → Langfuse span（v3 `start_span`，必要时回退 v2 `trace.span`）；
- metric → Langfuse score；
- 通过 `create_trace_id()` + `trace_context` 把一次请求的 span 归到同一 trace；
- 根 span 结束后重置 trace；
- `flush()` 输出缓冲；
- 所有调用防御式处理，Langfuse 异常不中断 Agent。

### 3.4 LangSmith provider

- span → `RunTree`（`run_type="chain"`）：`post()` → `end()` → `patch()`；
- metric → `create_feedback`（best-effort）；
- 所有调用防御式处理。

### 3.5 Composite provider

```python
class CompositeObservabilityProvider(ObservabilityProvider):
    def __init__(self, providers): ...
```

- `create_span`：向每个子 provider 创建子 span，记录映射；
- `end_span`：把共享 span 上后置设置的属性同步给子 span，再逐个结束；
- `record_metric`：逐个转发；
- **单个子 provider 异常只记录日志，不影响其它 provider 与主流程**。

### 3.6 配置化

新增 `vanna.config.ObservabilityConfig`：

- `backend`：`none | logging | otel | langfuse | langsmith | composite`
- `backends`：composite 时启用的后端列表
- `service_name`
- OTel：`otel_endpoint` / `otel_protocol` / `otel_headers`
- Langfuse：`langfuse_public_key` / `secret_key` / `host`
- LangSmith：`langsmith_api_key` / `api_url` / `project`
- `options` / `backend_options`：透传给具体后端

工厂：

```python
create_observability_provider(config)   # 返回 provider 或 None
apply_observability_config(agent, config)
```

---

## 4. 代码变动清单

### 4.1 新增文件

| 文件 | 说明 |
|---|---|
| `src/vanna/core/observability/composite.py` | `CompositeObservabilityProvider` |
| `src/vanna/integrations/local/observability.py` | `LoggingObservabilityProvider` |
| `src/vanna/integrations/otel/provider.py` | `OpenTelemetryObservabilityProvider` |
| `src/vanna/integrations/langfuse/provider.py` | `LangfuseObservabilityProvider` |
| `src/vanna/integrations/langsmith/provider.py` | `LangSmithObservabilityProvider` |
| `src/vanna/config/observability.py` | `ObservabilityConfig` + 工厂 |
| `tests/test_observability.py` | 15 个用例 |

### 4.2 修改文件

| 文件 | 改动 |
|---|---|
| `src/vanna/core/observability/__init__.py` | 导出 `CompositeObservabilityProvider` |
| `src/vanna/core/__init__.py` | 导出 `CompositeObservabilityProvider` |
| `src/vanna/integrations/local/__init__.py` | 导出 `LoggingObservabilityProvider` |
| `src/vanna/config/__init__.py` | 导出 `ObservabilityConfig` / 工厂 |
| `src/vanna/__init__.py` | 顶层导出 |
| `src/vanna/servers/fastapi/app.py` | 应用 `config["observability"]` |
| `src/vanna/servers/flask/app.py` | 同上 |
| `src/vanna/servers/cli/server_runner.py` | 新增 `--observability-*` / `--otel-endpoint`；支持 `VANNA_OBSERVABILITY_BACKEND` |
| `pyproject.toml` | 新增 `otel` / `langfuse` / `langsmith` extras |

### 关键代码

```python
# OTel：属性同步 + 结束
async def end_span(self, span):
    span.end()
    otel_span = self._otel_spans.pop(span.id, None)
    for key, value in span.attributes.items():
        otel_span.set_attribute(key, _coerce_attr(value))
    otel_span.set_attribute("duration_ms", span.duration_ms() or 0)
    otel_span.end()

# Composite：扇出 + 故障隔离
async def record_metric(self, name, value, unit="", tags=None):
    for provider in self.providers:
        try:
            await provider.record_metric(name, value, unit=unit, tags=tags)
        except Exception:
            logger.exception("provider failed")
```

---

## 5. 使用方式

### 5.1 Python

```python
from vanna import Agent, ObservabilityConfig, create_observability_provider

# OTel（标准，导出到任意 OTLP 后端）
provider = create_observability_provider(
    ObservabilityConfig(backend="otel", otel_endpoint="http://localhost:4318")
)

# Langfuse
provider = create_observability_provider(ObservabilityConfig(
    backend="langfuse",
    langfuse_public_key="pk-...", langfuse_secret_key="sk-...",
    langfuse_host="https://cloud.langfuse.com",
))

# LangSmith
provider = create_observability_provider(ObservabilityConfig(
    backend="langsmith", langsmith_api_key="lsv2_...", langsmith_project="vanna",
))

# 全部同时启用
provider = create_observability_provider(ObservabilityConfig(
    backend="composite", backends=["otel", "langfuse", "langsmith"],
))

agent = Agent(..., observability_provider=provider)
```

### 5.2 环境变量

```bash
VANNA_OBSERVABILITY_BACKEND=composite
VANNA_OBSERVABILITY_BACKENDS=otel,langfuse,langsmith
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer xxx
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=...
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=vanna
```

```python
from vanna.config import ObservabilityConfig
cfg = ObservabilityConfig.from_env()
```

### 5.3 服务端 JSON 配置

```json
{
  "observability": {
    "backend": "otel",
    "otel_endpoint": "http://localhost:4318",
    "service_name": "vanna"
  }
}
```

`VannaFastAPIServer` / `VannaFlaskServer` 构造时自动应用。

### 5.4 CLI

```bash
vanna --observability-backend otel --otel-endpoint http://localhost:4318
vanna --observability-backend composite --observability-backends otel,langfuse
vanna --observability-backend logging
```

### 5.5 安装 extras

```bash
pip install "vanna[otel]"
pip install "vanna[langfuse]"
pip install "vanna[langsmith]"
```

---

## 6. 验证

`tests/test_observability.py`（15 passed）：

- Composite 扇出：metric/span 同时到达多个 provider，后置属性同步；
- Composite 故障隔离：一个 provider 抛异常不影响另一个；
- Logging provider 输出；
- OTel provider（假 tracer/meter）属性序列化与 metric 记录；
- OTel provider + 真实 SDK `InMemorySpanExporter`：span 名称、属性、`duration_ms` 正确导出；
- Langfuse provider（假 client）：span/trace/score 映射；
- LangSmith provider（假 SDK 模块）：RunTree post/end/patch 与 feedback；
- 配置解析（dict/env）、非法后端校验、工厂分发、server 接线。

---

## 7. 兼容性与注意事项

1. **默认关闭**：不配置 `observability` 时 `observability_provider=None`，零开销，行为不变。
2. **驱动懒加载**：未安装 `opentelemetry` / `langfuse` / `langsmith` 时不影响 `import vanna`；只有真正选用对应后端才报 ImportError。
3. **OTel exporter**：`endpoint` 自动装配需要 `opentelemetry-exporter-otlp-proto-http`（或 gRPC）包；也可由应用自行配置全局 SDK 后传入 `tracer`/`meter`。
4. **Langfuse/LangSmith 版本**：Langfuse 做了 v3/v2 兼容回退；LangSmith 使用 `RunTree`。SDK 大版本升级时建议回归测试。
5. **敏感信息**：span 属性可能包含 SQL/用户信息，注意各平台的脱敏与采样配置。
6. **性能**：OTel 使用 BatchSpanProcessor / PeriodicExportingMetricReader，默认异步批量导出；进程退出前可调用 `force_flush()`。

---

## 8. 后续可扩展点

- 增加 OpenLLMetry / Traceloop 语义约定（`gen_ai.*` 属性）；
- 增加 Prometheus exporter（本地拉取）；
- 为 Langfuse 增加 generation（模型名、输入输出）而非仅 span；
- 增加采样率、脱敏策略配置；
- 将评测结果（P1 评测框架）写入 Langfuse/LangSmith。

---

## 附：提交与文件

- 提交：`23e0aa7`
- 新增：`src/vanna/core/observability/composite.py`、`src/vanna/integrations/{otel,langfuse,langsmith}/`、`src/vanna/config/observability.py`
- 测试：`tests/test_observability.py`
