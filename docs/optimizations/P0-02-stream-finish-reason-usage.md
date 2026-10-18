# P0-02 流式路径补回 finish_reason 与 usage

- **对应问题**：#2 流式路径丢失 `finish_reason` 与 `usage`
- **提交**：`23234c7 fix: LLM 集成改用异步 SDK 并补回流式 finish_reason/usage`
- **优先级**：P0（正确性/性能硬伤）
- **状态**：已完成

---

## 1. 问题背景

优化前，`Agent._handle_streaming_response` 聚合流式分片时只保留了文本与工具调用：

```python
# agent.py（优化前）
async def _stream_once() -> LlmResponse:
    accumulated_content = ""
    accumulated_tool_calls = []
    async for chunk in self.llm_service.stream_request(request):
        if chunk.content:
            accumulated_content += chunk.content
        if chunk.tool_calls:
            accumulated_tool_calls.extend(chunk.tool_calls)
    return LlmResponse(
        content=accumulated_content or None,
        tool_calls=accumulated_tool_calls or None,
    )
```

同时，`LlmStreamChunk` 模型里根本没有 `usage` 字段：

```python
class LlmStreamChunk(BaseModel):
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
```

后果：

1. **无法感知截断**：模型因 `max_tokens` 被截断时，`finish_reason` 会是 `length` / `max_tokens`，但流式模式下这个信息被丢弃，Agent 会把不完整回答当作正常结束。
2. **成本统计失效**：流式模式下拿不到 prompt/completion token 用量，无法做计费、配额与成本分析。
3. 各供应商适配器即使想上报 usage 也没有承载字段。

---

## 2. 主流方案参考

主流 LLM SDK / 框架在流式响应中都会带上终止元信息：

| 供应商 | 终止信息 | token 用量 |
|---|---|---|
| OpenAI | `choices[0].finish_reason`（`stop` / `length` / `tool_calls`） | 需显式 `stream_options.include_usage = true`，最后一个 chunk 的 `usage` 字段 |
| Azure OpenAI | 同 OpenAI | 同 OpenAI |
| Anthropic | `final.stop_reason`（`end_turn` / `max_tokens` / `tool_use`） | `final.usage.input_tokens` / `output_tokens` |
| Ollama | 终止 chunk 的 `done_reason`（`stop` / `length` ...） | `prompt_eval_count` / `eval_count` |

框架侧（OpenAI Agents、LangChain、Vercel AI SDK）都会把这些字段汇总到最终响应对象，供上层做截断判断、成本统计与评测。

---

## 3. 方案设计

### 3.1 扩展流式分片模型

`LlmStreamChunk` 增加可选的 `usage` 字段（向后兼容）：

```python
class LlmStreamChunk(BaseModel):
    ...
    # 结束原因
    finish_reason: Optional[str] = None
    # token 用量统计（通常在流的终止分片中给出）
    usage: Optional[Dict[str, int]] = None
    ...
```

### 3.2 Agent 聚合终止信息

在一次流式调用的聚合函数里累加 `finish_reason` / `usage`，并写回最终 `LlmResponse`：

```python
async def _stream_once() -> LlmResponse:
    accumulated_content = ""
    accumulated_tool_calls = []
    finish_reason = None
    usage = None

    async for chunk in self.llm_service.stream_request(request):
        if chunk.content:
            accumulated_content += chunk.content
        if chunk.tool_calls:
            accumulated_tool_calls.extend(chunk.tool_calls)

        # 收集结束原因与 token 用量（通常在终止分片中携带）
        if chunk.finish_reason:
            finish_reason = chunk.finish_reason
        if chunk.usage:
            usage = chunk.usage

    return LlmResponse(
        content=accumulated_content if accumulated_content else None,
        tool_calls=accumulated_tool_calls if accumulated_tool_calls else None,
        finish_reason=finish_reason,
        usage=usage,
    )
```

这样无论是流式还是非流式路径，`LlmResponse.finish_reason` / `usage` 都可用。

### 3.3 各供应商适配器上报

**OpenAI / Azure OpenAI**：请求时开启 usage，在终止 chunk 捕获。

```python
payload.setdefault("stream_options", {})["include_usage"] = True

usage = None
async for event in stream:
    if getattr(event, "usage", None):
        u = event.usage
        usage = {
            "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
        }
    ...

if final_tool_calls:
    yield LlmStreamChunk(
        tool_calls=final_tool_calls,
        finish_reason=last_finish,
        usage=usage,
    )
else:
    yield LlmStreamChunk(finish_reason=last_finish or "stop", usage=usage)
```

**Anthropic**：从 `final.usage` 提取，终止 chunk 同时带 `stop_reason`。

```python
usage = None
if getattr(final, "usage", None):
    try:
        usage = {
            "input_tokens": int(final.usage.input_tokens),
            "output_tokens": int(final.usage.output_tokens),
        }
    except Exception:
        usage = None

if tool_calls:
    yield LlmStreamChunk(
        tool_calls=tool_calls,
        finish_reason=getattr(final, "stop_reason", None),
        usage=usage,
    )
else:
    yield LlmStreamChunk(
        finish_reason=getattr(final, "stop_reason", None) or "stop",
        usage=usage,
    )
```

**Ollama**：在 `done` 分片映射 `prompt_eval_count` / `eval_count`。

```python
if chunk.get("done"):
    last_finish = chunk.get("done_reason", "stop")
    usage = {
        "prompt_tokens": chunk.get("prompt_eval_count", 0),
        "completion_tokens": chunk.get("eval_count", 0),
        "total_tokens": chunk.get("prompt_eval_count", 0)
        + chunk.get("eval_count", 0),
    }
```

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/llm/models.py` | `LlmStreamChunk` 新增 `usage: Optional[Dict[str, int]]` |
| `src/vanna/core/agent/agent.py` | `_handle_streaming_response._stream_once` 累加并返回 `finish_reason` / `usage` |
| `src/vanna/integrations/openai/llm.py` | `stream_options.include_usage = True`；捕获终止 chunk 的 usage；终止分片携带 `finish_reason` + `usage` |
| `src/vanna/integrations/azureopenai/llm.py` | 同 OpenAI |
| `src/vanna/integrations/anthropic/llm.py` | 从 `final.usage` 提取 input/output tokens；终止分片携带 usage |
| `src/vanna/integrations/ollama/llm.py` | `done` 分片映射 `prompt_eval_count`/`eval_count`；终止分片携带 usage |

> 说明：#1（异步 SDK）与 #2（finish_reason/usage）在同一个提交 `23234c7` 中完成，但属于两个独立问题，故拆成两份文档。

---

## 5. 使用方式

### 5.1 截断检测

```python
response = await agent._send_llm_request(request)  # 或流式路径聚合后的 response
if response.finish_reason in ("length", "max_tokens"):
    # 输出被截断，可提示用户或增大 max_tokens
    ...
```

流式路径同样可用，因为 `_handle_streaming_response` 现在会回填该字段。

### 5.2 成本统计 / 配额

```python
usage = response.usage or {}
prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
completion_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
```

> 注意不同供应商的键名不同：OpenAI/Azure/Ollama 用 `prompt_tokens`/`completion_tokens`，Anthropic 用 `input_tokens`/`output_tokens`。如需统一口径，可在 `LifecycleHook.after_message` / `LlmMiddleware.after_llm_response` 中做归一化。

### 5.3 通过中间件统一上报

```python
class UsageMiddleware(LlmMiddleware):
    async def after_llm_response(self, request, response):
        if response.usage:
            await metrics.record("llm.usage", response.usage)
        if response.finish_reason in ("length", "max_tokens"):
            await metrics.record("llm.truncated", 1)
        return response
```

---

## 6. 验证

- `tests/test_token_streaming_e2e.py`：验证流式工具循环与文本聚合正确（3 passed）。
- `tests/test_error_recovery.py`：流式聚合在错误恢复路径下仍能返回完整 `LlmResponse`。
- 新增字段为可选，既有 `LlmStreamChunk(...)` 构造无需修改，向后兼容。
- 供应商集成测试需真实 Key，默认跳过；配置 Key 后可验证 `usage` 与 `finish_reason` 是否回填。

手工验证建议：

```python
# 设置一个很小的 max_tokens，观察 finish_reason 是否为 length/max_tokens
response = await llm.send_request(LlmRequest(..., max_tokens=5))
print(response.finish_reason, response.usage)
```

流式路径同理，在 `Agent` 聚合后检查 `response.finish_reason` / `response.usage`。

---

## 7. 兼容性与注意事项

1. **向后兼容**：`usage` 为可选字段，旧代码不传也不会报错。
2. **供应商差异**：usage 键名不统一（见 5.2），框架只做透传，不强行归一化，避免丢失原始信息。
3. **OpenAI 兼容端点**：部分自建/代理端点不支持 `stream_options.include_usage`，如遇报错可关闭该选项或改用非流式路径。
4. **Anthropic**：`usage` 只在 `final` 消息中完整；流式过程中无法逐 token 统计。
5. **工具调用轮次**：一轮对话可能包含多次 LLM 调用（工具循环），`usage` 只反映单次调用；如需累计，应在 `Agent` 层或中间件中按 `request_id` 汇总。

---

## 8. 后续可扩展点

- 在 `Agent` 层累计整个工具循环的 token 用量，写入会话 metadata 或审计日志；
- 统一各供应商的 usage 键名（提供 `normalize_usage()` 工具函数）；
- 将 `finish_reason` 透传到 UI（例如截断时展示提示）；
- 在可观测性中记录 `llm.tokens` / `llm.truncated` 指标。

---

## 附：提交与文件

- 提交：`23234c7`
- 修改文件：
  - `src/vanna/core/llm/models.py`
  - `src/vanna/core/agent/agent.py`
  - `src/vanna/integrations/openai/llm.py`
  - `src/vanna/integrations/azureopenai/llm.py`
  - `src/vanna/integrations/anthropic/llm.py`
  - `src/vanna/integrations/ollama/llm.py`
