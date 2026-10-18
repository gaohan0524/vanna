# P1-16 工具循环触顶后的收尾总结

- **对应问题**：#16 `max_tool_iterations` 触顶后没有 LLM 总结，用户只收到警告卡片
- **优先级**：P1（体验/正确性）
- **状态**：已完成
- **提交**：`0b1df4d feat: 工具循环触顶后增加收尾总结（P1-16）`

---

## 1. 问题背景

优化前，工具循环达到 `max_tool_iterations` 时直接退出：

```python
if tool_iterations >= self.config.max_tool_iterations:
    yield StatusBarUpdateComponent(status="warning", message="Tool limit reached", ...)
    yield RichTextComponent(content="⚠️ **Tool Execution Limit Reached** ...")
    yield ChatInputUpdateComponent(...)
```

问题在于：**最后一次工具调用的结果已经写回会话，但 LLM 没有机会据此生成回答**。用户看到的是「达到上限」的警告，而不是「基于已查到数据的最佳答案」，体验与信息利用率都不好。

主流做法（OpenAI Agents SDK 的 final output、LangGraph 的 recursion limit 收尾、Claude Code 的「继续」提示）都会在触顶后尽量给出一个收尾回答。

---

## 2. 方案设计

### 2.1 新增配置开关

```python
class AgentConfig(BaseModel):
    max_tool_iterations: int = 10
    summarize_on_tool_limit: bool = True
```

默认开启：触顶后额外做一次「不带工具」的 LLM 调用；设为 `False` 则保持原有警告行为。

### 2.2 触顶时发起一次 tool-free 总结

```python
summary_request = request.model_copy(
    update={
        "tools": None,                       # 关键：禁用工具，避免再次进入工具循环
        "stream": self.config.stream_responses,
        "messages": [
            *request.messages,               # 已包含最后一次工具结果
            LlmMessage(role="user", content=TOOL_LIMIT_SUMMARY_PROMPT),
        ],
    }
)
```

`TOOL_LIMIT_SUMMARY_PROMPT` 明确要求：

- 不要再调用工具；
- 基于已收集的信息给出最佳最终回答；
- 如果任务未完成，简要说明还缺什么、用户下一步可以怎么做。

### 2.3 复用流式 / 非流式两条路径

```python
if self.config.stream_responses:
    summary_holder = {}
    async for component in self._stream_response_components(summary_request, summary_holder):
        yield component
    summary_response = summary_holder["response"]
else:
    summary_response = await self._send_llm_request(summary_request)
    yield RichTextComponent(content=summary_response.content, markdown=True)
```

- 流式：复用 token 级打字机；
- 非流式：一次性输出；
- 总结成功时写入 `conversation`（便于下一轮上下文与自动持久化）。

### 2.4 失败回退

总结调用异常时只记录 warning，然后回退到原有的完整警告文案，保证不会因为收尾总结失败而中断整个响应。

### 2.5 可观测性

在 `agent.send_message` 的 span 上增加：

```python
message_span.set_attribute("summarized_on_tool_limit", summary_text is not None)
```

便于统计「触顶时有多少比例成功产出了总结」。

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/agent/agent.py` | 新增 `TOOL_LIMIT_SUMMARY_PROMPT`；触顶分支增加 tool-free 总结（流式/非流式）、写入会话、失败回退、观测属性 |
| `src/vanna/core/agent/config.py` | 新增 `AgentConfig.summarize_on_tool_limit`（默认 `True`） |
| `tests/test_tool_limit_summary.py` | 新增 3 个用例 |

### 关键代码

```python
summary_text: Optional[str] = None
if tool_iterations >= self.config.max_tool_iterations:
    ...
    if self.config.summarize_on_tool_limit:
        summary_request = request.model_copy(
            update={
                "tools": None,
                "stream": self.config.stream_responses,
                "messages": [
                    *request.messages,
                    LlmMessage(role="user", content=TOOL_LIMIT_SUMMARY_PROMPT),
                ],
            }
        )
        ...
    if summary_text:
        yield note("The summary above is based on results gathered so far ...")
    else:
        yield 原有完整警告
```

---

## 4. 使用方式

默认生效，无需额外配置：

```python
agent = Agent(..., config=AgentConfig(max_tool_iterations=5))
```

如需关闭（例如严格控制 LLM 调用次数）：

```python
agent = Agent(
    ...,
    config=AgentConfig(max_tool_iterations=5, summarize_on_tool_limit=False),
)
```

---

## 5. 验证

`tests/test_tool_limit_summary.py`（3 passed）：

- `test_tool_limit_summarizes_when_enabled`：
  - 触顶后恰好一次 `tools=None` 的 LLM 调用；
  - 请求最后一条消息包含收尾提示词；
  - 输出包含总结文本与「tool-call limit」提示。
- `test_tool_limit_without_summary_when_disabled`：
  - 所有 LLM 调用都带 tools（没有收尾总结）；
  - 输出仍包含原有「Tool Execution Limit Reached」警告。
- `test_tool_limit_summary_streaming`：
  - 流式路径同样发起 `tools=None` 调用，并输出总结文本。

回归：相关测试集 **171 passed, 3 skipped**。

---

## 6. 兼容性与注意事项

1. **多一次 LLM 调用**：仅在触顶时发生；可用 `summarize_on_tool_limit=False` 关闭。
2. **不会再次触发工具循环**：总结请求显式 `tools=None`，模型无法再调用工具。
3. **总结质量**：取决于已有工具结果；若任务确实未完成，总结会说明缺口。
4. **会话写入**：总结作为 `assistant` 消息写入会话，下一轮可继续追问。
5. **错误隔离**：总结失败只记录日志并回退警告，不影响主流程。

---

## 7. 后续可扩展点

- 将收尾提示词做成可配置项（不同业务不同话术）；
- 为总结请求单独设置较小的 `max_tokens`，控制成本；
- 在 `metadata` 中标记 `summarized=true`，供前端展示不同 UI；
- 结合评测框架统计「触顶场景答案完整率」。

---

## 附：文件

- 修改：`src/vanna/core/agent/agent.py`、`src/vanna/core/agent/config.py`
- 测试：`tests/test_tool_limit_summary.py`
