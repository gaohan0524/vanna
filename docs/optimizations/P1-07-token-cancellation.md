# P1-07 Token 级中断/取消

- **对应问题**：#7 无 token 级中断/取消
- **补充问题**：用户无法中途停止生成；服务端也无法感知客户端中断，正在进行的 LLM 流式调用会继续消耗 token。
- **提交**：`6d4650f feat: 支持 token 级中断/取消并默认使用极简前端`
- **优先级**：P1（功能/体验差距）
- **状态**：已完成

---

## 1. 问题背景

优化前，聊天链路是「一次性跑到黑」：

```
POST /chat_sse → ChatHandler.handle_stream → Agent.send_message
  → 工具循环 → _stream_response_components → llm_service.stream_request
```

- 没有取消信号：一旦开始，必须等模型输出完或工具循环结束。
- 客户端关闭页面/断开连接时，服务端没有感知，底层 LLM HTTP 流仍在进行。
- WebSocket 也没有显式的 `cancel` 协议。

而主流方案（Vercel AI SDK 的 `AbortController.abort()`、Claude Code 关闭 SSE 连接、OpenAI SDK 关闭 async iterator）都会在用户点击「停止」时立即中止底层流式请求。

---

## 2. 主流方案参考

| 方案 | 取消机制 |
|---|---|
| **Vercel AI SDK** | 客户端 `AbortController.abort()`；服务端用 `request.signal` 把取消传播给模型流。 |
| **Claude Code / Anthropic** | 关闭 SSE 连接 / 调用 SDK 的 `.close()` 中止底层流。 |
| **OpenAI** | 关闭 `Stream` async iterator（`aclose()`）。 |

共同点：

1. **传输层检测取消**：HTTP/SSE 看客户端是否断开；WebSocket 收显式 `cancel` 帧。
2. **协作式取消**：用 `asyncio.Event` / `CancelledError` 把信号传到正在 `await` 的 LLM 流式调用，`cancel()` 掉对应 task，关闭到模型供应商的连接。

---

## 3. 方案设计

### 3.1 取消信号：`asyncio.Event`

在整条链路中新增可选参数 `cancel_event: Optional[asyncio.Event]`：

```
ChatHandler.handle_stream(request, cancel_event)
  → Agent.send_message(..., cancel_event)
    → Agent._send_message(..., cancel_event)
      → Agent._stream_response_components(..., cancel_event)
```

传输层负责置位事件，Agent 负责响应。

### 3.2 为什么不能只靠 `GeneratorExit` / `aclose()`

一个关键实现难点：`_stream_response_components` 的主循环会阻塞在：

```python
kind, payload = await token_queue.get()
```

此时异步生成器**并未挂起在 `yield` 点**，而是正在 `await`。对这种生成器调用 `aclose()` 会报 `RuntimeError: aclose(): asynchronous generator is already running`，无法打断。

因此采用「**取消 watcher + 队列哨兵**」：

```python
async def _watch_cancel() -> None:
    await cancel_event.wait()
    # 1) 中止在途 LLM 流式调用（关闭底层连接、停止 token 计费）
    if not task.done():
        task.cancel()
    # 2) 用哨兵唤醒可能阻塞在 token_queue.get() 的主循环
    await token_queue.put(("cancelled", None))
```

主循环收到 `("cancelled", None)` 或检测到 `cancel_event.is_set()` 后抛 `asyncio.CancelledError`，沿 `async for` 链向上传播。

### 3.3 为什么用 `CancelledError` 而不是自定义异常

- `asyncio.CancelledError` 在 Python 3.8+ 继承自 `BaseException`，**不会被 `send_message` 的 `except Exception` 兜底成错误卡片**，而是原样传播给传输层。
- 传输层可以据此区分「用户取消」与「业务异常」，分别收尾。

### 3.4 工具循环检查点

除了流式 token 等待，工具执行也可能耗时。因此在两处加检查：

```python
while tool_iterations < self.config.max_tool_iterations:
    self._raise_if_cancelled(cancel_event)   # 每轮开始
    ...
    executed = await asyncio.gather(...)      # 工具并行执行
    self._raise_if_cancelled(cancel_event)   # 执行结束后
```

---

## 4. 代码变动清单

### 4.1 修改文件

| 文件 | 改动 |
|---|---|
| `src/vanna/core/agent/agent.py` | `send_message` / `_send_message` 增加 `cancel_event`；新增 `_raise_if_cancelled`；`_stream_response_components` 增加取消 watcher、哨兵与 `finally` 清理 |
| `src/vanna/servers/base/chat_handler.py` | `handle_stream(request, cancel_event=None)` 原样透传给 Agent |
| `src/vanna/servers/fastapi/routes.py` | SSE 增加客户端断开 watcher；WebSocket 支持 `{"type":"cancel"}` 帧与断开取消；新增 `_await_task` 清理助手 |
| `src/vanna/servers/flask/routes.py` | `generate()` 的 `finally` 中显式 `gen.aclose()`，断开时尽力取消底层任务 |
| `src/vanna/servers/base/templates.py` | 新增 `get_simple_chat_html()`（含「停止」按钮），`get_index_html()` 默认返回它；原富前端保留为 `get_rich_index_html()` |
| `src/vanna/examples/test.py` | 移除内联 HTML 与自定义 `/` 路由，改用公共默认前端（该文件在 `.gitignore` 中） |
| `tests/test_stream_cancellation.py` | 新增取消测试 |

### 4.2 关键实现片段

**Agent：取消 watcher 与哨兵**

```python
task = asyncio.create_task(_worker())

async def _watch_cancel() -> None:
    await cancel_event.wait()
    if not task.done():
        task.cancel()
    await token_queue.put(("cancelled", None))

watcher = asyncio.create_task(_watch_cancel()) if cancel_event else None

try:
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError()
        kind, payload = await token_queue.get()
        if kind == "cancelled":
            raise asyncio.CancelledError()
        ...
finally:
    if watcher is not None:
        watcher.cancel()
    if not task.done():
        task.cancel()
```

**FastAPI SSE：断开检测**

```python
cancel_event = asyncio.Event()

async def watch_disconnect() -> None:
    while not cancel_event.is_set():
        if await http_request.is_disconnected():
            cancel_event.set()
            break
        await asyncio.sleep(0.1)

watcher = asyncio.create_task(watch_disconnect())
try:
    async for chunk in chat_handler.handle_stream(chat_request, cancel_event=cancel_event):
        yield f"data: {chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"
except asyncio.CancelledError:
    raise
finally:
    watcher.cancel()
```

**FastAPI WebSocket：cancel 帧与断开**

```python
stream_task = asyncio.create_task(stream_and_send())

while not stream_task.done():
    recv_task = asyncio.create_task(websocket.receive_json())
    done, pending = await asyncio.wait(
        {stream_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
    )
    if stream_task in done:
        recv_task.cancel()
        await _await_task(recv_task)
        break

    control = recv_task.result()
    if isinstance(control, dict) and control.get("type") == "cancel":
        cancel_event.set()
        await _await_task(stream_task)
        break
```

取消后 `stream_and_send` 会回发：

```json
{"type": "cancelled", "data": {"status": "cancelled"}, ...}
```

**Flask：断开时关闭异步生成器**

```python
finally:
    if gen is not None:
        try:
            loop.run_until_complete(gen.aclose())
        except Exception:
            pass
    loop.close()
```

> 同步 WSGI 无法主动探测断开，只能依赖「写下一个分片时抛错/生成器被关闭」这一信号，属尽力而为。

**前端：默认极简页的「停止」按钮**

```html
<form id="form">
  <input id="input" ...>
  <button type="submit">发送</button>
  <button id="stop" type="button" disabled>停止</button>
</form>
```

```javascript
stopBtn.addEventListener('click', function () {
  if (abortController) abortController.abort();
});
```

`AbortController.abort()` 关闭 `fetch` 连接 → 服务端 `is_disconnected()` 检测到 → 置位 `cancel_event` → 中止 LLM 流。

---

## 5. 使用方式

### 5.1 服务端无需额外配置

取消能力默认启用；现有 `Agent` / `ChatHandler` 调用方式不变（`cancel_event` 是可选参数）。

### 5.2 前端

- 默认首页（`get_simple_chat_html`）自带「停止」按钮。
- 自定义前端可以：
  - **SSE**：使用 `AbortController` 中断 `fetch`（推荐），或直接关闭连接；
  - **WebSocket**：发送 `{"type": "cancel"}`。

### 5.3 自定义传输层（如自己的 HTTP 框架）

```python
cancel_event = asyncio.Event()

async for chunk in chat_handler.handle_stream(request, cancel_event=cancel_event):
    await send(chunk)

# 在客户端断开/用户点击停止时：
cancel_event.set()
```

---

## 6. 验证

- `tests/test_stream_cancellation.py`（2 passed）
  - 设置 `cancel_event` 后 `send_message` 抛 `asyncio.CancelledError`，且 LLM 未产出全部 token；
  - 未传 `cancel_event` 时行为完全不变（完整流式回答）。
- 既有 token 流式测试 `tests/test_token_streaming_e2e.py` 全部通过（3 passed），确认工具循环未被取消逻辑破坏。
- 手工验证：
  - SSE 页面点击「停止」后，服务端日志显示 LLM 流被 cancellation 中止；
  - WebSocket 发送 `{"type":"cancel"}` 能收到 `cancelled` 帧。

---

## 7. 兼容性与注意事项

1. **默认行为不变**：不传 `cancel_event` 时，取消 watcher 不创建，零额外开销。
2. **错误卡片不受影响**：业务异常仍走 `send_message` 的 `except Exception` 兜底；只有 `CancelledError` 会向上传播。
3. **Flask 限制**：同步 WSGI 无法主动探测断开，取消依赖生成器被关闭，属尽力而为；对取消实时性要求高的场景建议用 FastAPI/ASGI。
4. **统计口径**：被取消的请求不会产生「完整回答」，若做用量统计需要把 `CancelledError` 单独归类。
5. **工具执行**：工具并行执行期间收到取消，会在 `asyncio.gather` 返回后立即退出，不会继续把结果回喂 LLM；已在执行的工具不会被强制中断（由工具自身超时控制）。

---

## 8. 后续可扩展点

- 在 WebSocket 协议中增加 `{"type":"cancel","request_id":...}`，支持同一连接多请求并发时精确取消指定请求；
- 为工具执行也引入可取消的 `cancel_event`（例如传给 `SqlRunner`，配合数据库原生取消）；
- 在可观测性中记录取消次数/取消耗时（`agent.cancelled` metric）。

---

## 附：提交与文件

- 提交：`6d4650f`
- 核心文件：`src/vanna/core/agent/agent.py`、`src/vanna/servers/base/chat_handler.py`、`src/vanna/servers/fastapi/routes.py`、`src/vanna/servers/flask/routes.py`、`src/vanna/servers/base/templates.py`
- 测试：`tests/test_stream_cancellation.py`、`tests/test_token_streaming_e2e.py`
