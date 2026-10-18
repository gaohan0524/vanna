# P1-04 Token 级流式输出（打字机效果）

- **对应问题**：#4 token 级流式未实现
- **提交**：`fca7ceb feat: token 级流式输出 + ollama 文本形式 tool call 兜底解析`
- **优先级**：P1（功能/体验差距）
- **状态**：已完成

---

## 1. 问题背景

优化前，流式路径是「组件级流式」：`_handle_streaming_response` 把整个 LLM 流消费完、聚合成一个 `LlmResponse` 后，才一次性 `yield` 一个完整文本组件。

```python
# 优化前（示意）
response = await self._handle_streaming_response(request)
yield RichTextComponent(content=response.content)
```

后果：

- 前端拿不到 token 级增量，只能等整个回答生成完毕才显示；
- 与 Vercel AI SDK / OpenAI SSE 的「打字机」体验差距明显；
- 长回答的首字延迟很高。

---

## 2. 主流方案参考

| 方案 | 做法 |
|---|---|
| OpenAI SSE | 逐 `delta.content` 推送 |
| Vercel AI SDK | `streamText` 逐 token 回调，前端增量渲染 |
| Claude Code / Anthropic | `text_stream` 逐片段消费 |

框架层的通用做法：**LLM 流式回调 → 立即产出 UI 增量组件（同一 id + `lifecycle=update`）→ 前端按 id 覆盖渲染**。

---

## 3. 方案设计

### 3.1 `on_content` 回调

给 `_handle_streaming_response` 增加可选回调：

```python
async def _handle_streaming_response(
    self,
    request: LlmRequest,
    on_content: Optional[Callable[[str], Awaitable[None]]] = None,
) -> LlmResponse:
    ...
    async for chunk in self.llm_service.stream_request(request):
        if chunk.content:
            accumulated_content += chunk.content
            if on_content is not None:
                await on_content(chunk.content)  # 实时推送增量
        ...
```

聚合结果仍返回完整 `LlmResponse`，保证工具循环逻辑不变。

### 3.2 `_stream_response_components`：队列 + worker

用 `asyncio.Queue` + 后台 task 解耦「消费 LLM 流」与「产出 UI 组件」：

```python
async def _stream_response_components(self, request, response_holder):
    token_queue = asyncio.Queue()
    text_id = str(uuid.uuid4())
    accumulated = ""
    first_chunk = True

    async def _on_content(text: str) -> None:
        await token_queue.put(("content", text))

    async def _worker() -> None:
        try:
            response = await self._handle_streaming_response(request, on_content=_on_content)
            await token_queue.put(("done", response))
        except Exception as e:
            await token_queue.put(("error", e))

    task = asyncio.create_task(_worker())
    try:
        while True:
            kind, payload = await token_queue.get()
            if kind == "content":
                accumulated += payload
                lifecycle = ComponentLifecycle.CREATE if first_chunk else ComponentLifecycle.UPDATE
                first_chunk = False
                yield UiComponent(
                    rich_component=RichTextComponent(
                        id=text_id, content=accumulated, markdown=True, lifecycle=lifecycle
                    ),
                    simple_component=SimpleTextComponent(text=accumulated),
                )
            elif kind == "done":
                response_holder["response"] = payload
                break
            else:
                raise payload
        await task
    finally:
        if not task.done():
            task.cancel()
```

要点：

- **同一 `text_id`**：第一次 `CREATE`，后续 `UPDATE`，前端按 id 覆盖同一段文本，实现打字机效果。
- **`response_holder`**：聚合完成后的 `LlmResponse` 写回主循环，后续工具判定/工具循环不受影响。
- **异常传播**：worker 捕获的异常通过队列重新在主生成器抛出，保持原有错误处理语义。

### 3.3 避免重复输出完整文本

工具循环中增加 `streamed_text` 标记：

```python
streamed_text = False
if self.config.stream_responses:
    response_holder = {}
    async for component in self._stream_response_components(request, response_holder):
        yield component
        streamed_text = True
    response = response_holder["response"]
```

后续分支在 `yield` 完整文本前检查 `if not streamed_text`，避免同一段回答出现两次。

### 3.4 Ollama 文本形式 tool call 兜底

小模型（如 qwen 系列）有时不走 `tool_calls` 通道，而是把工具调用 JSON 直接写进正文。新增解析兜底：

```python
def _iter_json_objects(text): ...          # 扫描文本中所有平衡 JSON 对象
def _normalize_tool_obj(obj): ...          # 归一化 {name, arguments} / {function:{...}}
def _parse_textual_tool_calls(content, tool_names): ...  # 仅认领已注册工具名
```

- `send_request`：正文里解析出工具调用后，剥离 JSON 文本并返回 `tool_calls`；
- `stream_request`：流结束后对累积正文做同样解析；
- 只认领「本次请求已注册的工具名」，避免把普通 JSON 误判为工具调用。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/agent/agent.py` | `_handle_streaming_response` 增加 `on_content`；新增 `_stream_response_components`；工具循环接入 token 级组件与 `streamed_text` |
| `src/vanna/core/agent/config.py` | `stream_responses` 注释更新为「token 级打字机」 |
| `src/vanna/integrations/ollama/llm.py` | 新增文本形式 tool call 解析与兜底（`_iter_json_objects` / `_normalize_tool_obj` / `_parse_textual_tool_calls`） |
| `tests/test_token_streaming_e2e.py` | token 级流式 + 工具循环端到端测试 |
| `tests/test_ollama_textual_tool_calls.py` | 文本形式 tool call 解析测试 |
| `tests/test_error_recovery.py` | 流式聚合与错误恢复测试补充 |

---

## 5. 使用方式

默认启用（`AgentConfig.stream_responses=True`）：

```python
agent = Agent(..., config=AgentConfig(stream_responses=True))
```

若要关闭打字机、退回一次性完整文本：

```python
agent = Agent(..., config=AgentConfig(stream_responses=False))
```

前端按组件 `id` + `lifecycle` 增量渲染：

```
第 1 个分片：{id: "abc", lifecycle: "create", text: "今天"}
第 2 个分片：{id: "abc", lifecycle: "update", text: "今天天气"}
第 3 个分片：{id: "abc", lifecycle: "update", text: "今天天气不错"}
```

---

## 6. 验证

- `tests/test_token_streaming_e2e.py`
  - 流式路径：说明文字 + tool_call → 工具执行 → 最终答案，循环完整；
  - 存在 token 级 `TEXT` 组件，且最终文本完整；
  - 非流式路径回归正常。
- `tests/test_ollama_textual_tool_calls.py`：正文 JSON 工具调用能被正确识别并剥离。
- `tests/test_error_recovery.py`：错误恢复重试后仍能正确聚合。

---

## 7. 兼容性与注意事项

1. **重试会重推 token**：若 `error_recovery_strategy` 触发重试，同一 `text_id` 的文本会被重新推送，前端需容忍「同 id 文本被重写」。
2. **工具调用轮次**：每轮 LLM 调用使用新的 `text_id`，避免不同轮次文本互相覆盖。
3. **非流式路径**：`stream_responses=False` 时行为与优化前一致。
4. **Ollama 文本兜底**：仅当正文 JSON 的 `name` 命中本次注册工具名时才认领，降低误判。
5. **性能**：每个 token 都会构造一个 `UiComponent` 并序列化；对高频 token 场景可考虑按时间/长度批量合并（未来优化）。

---

## 8. 后续可扩展点

- 对 token 做小批量合并（如每 20ms 或每 N 个字符推送一次），降低组件序列化开销；
- 在前端增加「正在输入」状态与取消按钮（取消见 P1-07）；
- 将 `finish_reason` / `usage` 透传到 UI（见 P0-02）。

---

## 附：提交与文件

- 提交：`fca7ceb`
- 文件：`src/vanna/core/agent/agent.py`、`src/vanna/core/agent/config.py`、`src/vanna/integrations/ollama/llm.py`
- 测试：`tests/test_token_streaming_e2e.py`、`tests/test_ollama_textual_tool_calls.py`、`tests/test_error_recovery.py`
