# P0-01 LLM 集成改用异步 SDK（消除事件循环阻塞）

- **对应问题**：#1 LLM 集成用同步阻塞 SDK，在 async 事件循环里阻塞线程
- **提交**：`23234c7 fix: LLM 集成改用异步 SDK 并补回流式 finish_reason/usage`
- **优先级**：P0（正确性/性能硬伤）
- **状态**：已完成

---

## 1. 问题背景

优化前，多个 LLM 供应商适配器虽然方法签名是 `async def`，内部却调用**同步 SDK**：

```python
# openai/llm.py（优化前）
resp = self._client.chat.completions.create(**payload, stream=False)
# 注释原文：Call the API synchronously; this function is async but we can block here.

# anthropic/llm.py（优化前）
resp = self._client.messages.create(**payload)
```

涉及：

| 文件 | 优化前客户端 | 阻塞点 |
|---|---|---|
| `src/vanna/integrations/openai/llm.py` | `openai.OpenAI` | `chat.completions.create(...)`、流式 `for event in stream` |
| `src/vanna/integrations/anthropic/llm.py` | `anthropic.Anthropic` | `messages.create(...)`、`with messages.stream(...)` |
| `src/vanna/integrations/azureopenai/llm.py` | `openai.AzureOpenAI` | 同 OpenAI |
| `src/vanna/integrations/ollama/llm.py` | `ollama.Client` | `chat(...)`、流式 `for chunk in stream` |

`async def` 只是让函数返回协程，**并不会把内部同步调用变成非阻塞**。同步 HTTP 请求会占住事件循环线程，导致：

- 单个 LLM 调用期间，同一事件循环上的其它请求（聊天、健康检查、WebSocket 心跳）全部停摆；
- 并发数上升时吞吐量坍塌（实际退化为串行）；
- 流式场景下，同步迭代器在读取 token 时也会阻塞。

> 参考：`openai/responses.py` 当时已经使用 `AsyncOpenAI`，属于正确写法；其余适配器未对齐。

---

## 2. 主流方案参考

主流框架在异步运行时一律使用异步 SDK：

| 框架 | 做法 |
|---|---|
| OpenAI Agents SDK | `AsyncOpenAI` + `await client.chat.completions.create(...)` |
| LangChain | `ChatOpenAI` / `ChatAnthropic` 的 async 接口内部使用异步 httpx 客户端 |
| LlamaIndex | `llm.acomplete()` / `astream_complete()` 走异步客户端 |
| Anthropic SDK | `AsyncAnthropic`、`async with client.messages.stream(...)` |

统一模式：

```python
client = AsyncXxxClient(...)          # 构造异步客户端
resp = await client.create(...)       # await 非流式调用
async for chunk in await client.stream(...):  # async for 消费流
    ...
```

---

## 3. 方案设计

### 3.1 替换异步客户端

| 适配器 | 优化前 | 优化后 |
|---|---|---|
| OpenAI | `OpenAI` | `AsyncOpenAI` |
| Azure OpenAI | `AzureOpenAI` | `AsyncAzureOpenAI` |
| Anthropic | `Anthropic` | `AsyncAnthropic` |
| Ollama | `ollama.Client` | `ollama.AsyncClient` |

### 3.2 非流式调用加 `await`

```python
resp = await self._client.chat.completions.create(**payload, stream=False)
```

### 3.3 流式调用改为异步迭代

**OpenAI / Azure OpenAI**

```python
stream = await self._client.chat.completions.create(**payload, stream=True)
async for event in stream:
    ...
```

**Anthropic**（上下文管理器也要异步化）

```python
async with self._client.messages.stream(**payload) as stream:
    async for text in stream.text_stream:
        if text:
            yield LlmStreamChunk(content=text)
    final = await stream.get_final_message()
```

**Ollama**

```python
stream = await self._client.chat(**payload, stream=True)
async for chunk in stream:
    ...
```

### 3.4 保持对外接口不变

`LlmService` 抽象（`send_request` / `stream_request` / `validate_tools`）与 `Agent` 调用方式完全不变；改动只发生在各适配器内部，因此对上层零侵入。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/integrations/openai/llm.py` | `AsyncOpenAI`；`await create(...)`；流式改 `async for` |
| `src/vanna/integrations/azureopenai/llm.py` | `AsyncAzureOpenAI`；`await create(...)`；流式改 `async for` |
| `src/vanna/integrations/anthropic/llm.py` | `AsyncAnthropic`；`await messages.create(...)`；`async with messages.stream(...)` + `async for text_stream` + `await get_final_message()` |
| `src/vanna/integrations/ollama/llm.py` | `ollama.AsyncClient`；`await chat(...)`；流式改 `async for` |

### 关键代码片段

**OpenAI**

```python
try:
    from openai import AsyncOpenAI
...
self._client = AsyncOpenAI(**client_kwargs)

# 非流式
resp = await self._client.chat.completions.create(**payload, stream=False)

# 流式
stream = await self._client.chat.completions.create(**payload, stream=True)
async for event in stream:
    ...
```

**Anthropic**

```python
self._client = anthropic.AsyncAnthropic(**client_kwargs)

resp = await self._client.messages.create(**payload)

async with self._client.messages.stream(**payload) as stream:
    async for text in stream.text_stream:
        if text:
            yield LlmStreamChunk(content=text)
    final = await stream.get_final_message()
```

**Ollama**

```python
self._client = ollama.AsyncClient(host=self.host, timeout=timeout)

resp = await self._client.chat(**payload)
stream = await self._client.chat(**payload, stream=True)
async for chunk in stream:
    ...
```

---

## 5. 影响与验证

### 5.1 行为影响

- 对调用方**无 API 变更**，返回结构与异常语义保持一致。
- 并发能力恢复：LLM 等待期间事件循环可继续处理其它请求。
- 流式输出不再因同步迭代阻塞事件循环。

### 5.2 验证方式

- 既有单元测试全部通过（`MockLlmService` 等不依赖真实供应商）。
- 供应商集成测试需要真实 API Key，由 `tests/conftest.py` 自动跳过；建议在配置了 Key 的环境跑：
  ```bash
  tox -e py311-anthropic
  tox -e py311-openai
  ```
- 手工并发验证：同时发起多个 `/chat_sse` 请求，观察服务端不再出现「一个请求卡住其它请求」的现象。

---

## 6. 兼容性与注意事项

1. **客户端构造参数**：`extra_client_kwargs` 现在透传给异步客户端，参数名与同步客户端一致（如 `base_url`、`timeout`）。
2. **关闭/清理**：异步客户端通常无需显式关闭；长生命周期进程可在退出时调用 SDK 提供的关闭方法。
3. **Ollama 版本**：`ollama.AsyncClient` 需要较新的 `ollama` Python 包；旧版本请升级。
4. **事件循环绑定**：异步客户端应在同一个事件循环中使用（FastAPI/uvicorn 单循环场景天然满足）。
5. **重试/超时**：异步化后原有超时参数仍然生效；如需更细粒度控制，可在 `Agent` 的 `error_recovery_strategy` 中统一处理。

---

## 7. 后续可扩展点

- 为异步客户端增加连接池/并发上限配置；
- 在 `Agent` 层增加 LLM 并发信号量（`asyncio.Semaphore`），防止瞬时打满供应商配额；
- 补一份「并发压测」脚本，纳入 CI 可选任务。

---

## 附：提交与文件

- 提交：`23234c7`
- 修改文件：
  - `src/vanna/integrations/openai/llm.py`
  - `src/vanna/integrations/azureopenai/llm.py`
  - `src/vanna/integrations/anthropic/llm.py`
  - `src/vanna/integrations/ollama/llm.py`
  - `src/vanna/core/agent/agent.py`（同提交内一并补回流式 finish_reason/usage，详见 P0-02）
  - `src/vanna/core/llm/models.py`（新增 `LlmStreamChunk.usage`，详见 P0-02）
