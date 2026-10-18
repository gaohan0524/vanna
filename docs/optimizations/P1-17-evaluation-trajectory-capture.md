# P1-17 评测轨迹采集（LLM 请求 + 工具调用）

- **对应问题**：#17 `EvaluationRunner` 的 `tool_calls` / `llm_requests` 恒为空，轨迹评测失效
- **优先级**：P1（评测/CI 能力）
- **状态**：已完成
- **提交**：`bfe64e8 feat: 评测轨迹采集（LLM 请求 + 工具调用）（P1-17）`

---

## 1. 问题背景

优化前，`EvaluationRunner._execute_agent` 只收集 UI 组件：

```python
components = []
tool_calls = []
error = None
async for component in agent.send_message(...):
    components.append(component)

# TODO: Extract tool calls and LLM requests from observability
return AgentResult(
    ...,
    tool_calls=tool_calls,     # 恒为 []
    llm_requests=[],           # 恒为 []
)
```

后果：

- `TrajectoryEvaluator` 依赖 `AgentResult.get_tool_names_called()`，但它永远拿到空列表；
- 期望「应调用 `run_sql`」「不应调用 `write_file`」等轨迹断言全部失效；
- 评测只能看最终输出，无法评估「智能体走了什么路径」。

主流评测框架（LangSmith evals、LangChain callbacks、OpenAI evals trace）都会在运行期间采集 LLM 请求与工具调用，作为轨迹评测的数据源。

---

## 2. 方案设计

### 2.1 用 `LlmMiddleware` 采集

`LlmMiddleware` 天然覆盖每次 LLM 调用：

```python
class _TrajectoryCaptureMiddleware(LlmMiddleware):
    async def before_llm_request(self, request):
        # 记录 messages / tools / system_prompt
        ...

    async def after_llm_response(self, request, response):
        # 从 response.tool_calls 提取 tool_name / arguments / id
        ...
```

- `before_llm_request`：记录每次发往 LLM 的请求；
- `after_llm_response`：记录模型「请求调用」的工具（这正是轨迹评测需要的）。

### 2.2 并发隔离：`ContextVar`

评测默认并发跑多个用例。为避免不同用例的数据串到一起，使用 `ContextVar` 把采集容器绑定到当前 task：

```python
_EVAL_CAPTURE: ContextVar[Optional[Dict[str, List[Dict[str, Any]]]]] = ContextVar(
    "vanna_eval_capture", default=None
)
```

- `_execute_agent` 开始时 `_EVAL_CAPTURE.set(capture)`，结束时 `reset(token)`；
- 每个用例在独立 task 中执行，ContextVar 天然隔离；
- 中间件通过 `_EVAL_CAPTURE.get()` 拿到「当前用例」的容器。

### 2.3 中间件生命周期

在 `_run_test_cases_parallel` 中临时挂载、结束后移除：

```python
capture_middleware = _TrajectoryCaptureMiddleware()
agent.llm_middlewares.append(capture_middleware)
try:
    tasks = [...]
    return await asyncio.gather(*tasks)
finally:
    agent.llm_middlewares = [
        m for m in agent.llm_middlewares if m is not capture_middleware
    ]
```

- 不会污染 Agent 的常规运行；
- 若 Agent 上已有采集中间件（嵌套评测），不会重复挂载。

### 2.4 填充 `AgentResult`

```python
return AgentResult(
    test_case_id=test_case.id,
    components=components,
    tool_calls=capture["tool_calls"],
    llm_requests=capture["llm_requests"],
    error=error,
)
```

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/evaluation/runner.py` | 新增 `_EVAL_CAPTURE` ContextVar 与 `_TrajectoryCaptureMiddleware`；`_run_test_cases_parallel` 临时挂载/移除中间件；`_execute_agent` 设置/重置 ContextVar 并填充 `AgentResult` |
| `tests/test_evaluation_trajectory.py` | 新增 2 个用例：采集正确性、并发隔离 |

### 关键代码

```python
class _TrajectoryCaptureMiddleware(LlmMiddleware):
    async def before_llm_request(self, request):
        capture = _EVAL_CAPTURE.get()
        if capture is not None:
            capture["llm_requests"].append({
                "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                "tools": [t.name for t in (request.tools or [])],
                "system_prompt": request.system_prompt,
            })
        return request

    async def after_llm_response(self, request, response):
        capture = _EVAL_CAPTURE.get()
        if capture is not None:
            for tool_call in response.tool_calls or []:
                capture["tool_calls"].append({
                    "tool_name": tool_call.name,
                    "arguments": tool_call.arguments,
                    "tool_call_id": tool_call.id,
                })
        return response
```

---

## 4. 使用方式

对外 API 不变，直接使用即可：

```python
from vanna.core.evaluation import EvaluationRunner, TrajectoryEvaluator, TestCase, ExpectedOutcome

runner = EvaluationRunner(evaluators=[TrajectoryEvaluator()])
report = await runner.run_evaluation(agent, test_cases)

for result in report.results:
    print(result.agent_result.tool_calls)
    # [{"tool_name": "run_sql", "arguments": {...}, "tool_call_id": "..."}]
    print(result.agent_result.llm_requests)
    # [{"messages": [...], "tools": ["run_sql", ...], "system_prompt": "..."}]
```

`TrajectoryEvaluator` 现在会真正基于 `tool_calls` 判断：

```python
ExpectedOutcome(tools_called=["run_sql"], tools_not_called=["write_file"])
```

---

## 5. 验证

`tests/test_evaluation_trajectory.py`（2 passed）：

- `test_evaluation_captures_llm_requests_and_tool_calls`：
  - 一次 `echo` 工具调用被记录；
  - 两次 LLM 请求被记录（tool_call 请求 + 最终答案请求）；
  - `TrajectoryEvaluator` 通过；
  - 评测结束后临时中间件已移除。
- `test_evaluation_capture_is_isolated_across_concurrent_cases`：
  - 4 个用例并发执行，每个用例的 `tool_calls` / `llm_requests` 都是各自独立的一份。

回归：相关测试集 **173 passed, 3 skipped**。

---

## 6. 兼容性与注意事项

1. **对外 API 不变**：无需修改测试用例或 Agent 配置。
2. **`llm_requests` 是简化快照**：记录消息 role/content、工具名、系统提示词；不含完整请求对象。
3. **中间件的临时性**：评测期间会临时修改 `agent.llm_middlewares`，结束后恢复；**不要在同一时刻用两个 `EvaluationRunner` 对同一个 Agent 实例做并发评测**（会共享同一个采集中间件，虽然 ContextVar 仍能隔离数据，但中间件会被重复挂载/移除）。
4. **流式/非流式都覆盖**：中间件在 `_send_llm_request` 与 `_handle_streaming_response` 两条路径都会被调用。
5. **工具结果**：当前只采集「请求调用的工具」；工具执行结果/成功与否可从 UI 组件或后续扩展中补充。

---

## 7. 后续可扩展点

- 把工具执行结果（`ToolResult`）与 `tool_call_id` 关联，支持更细粒度的轨迹评测；
- 将轨迹写入 OTel / Langfuse / LangSmith，与可观测性统一；
- 在 `EvaluationReport` 中展示每个用例的工具调用序列；
- 为 `llm_requests` 增加 token 用量（结合 P0-02 的 `usage`）。

---

## 附：文件

- 修改：`src/vanna/core/evaluation/runner.py`
- 测试：`tests/test_evaluation_trajectory.py`
