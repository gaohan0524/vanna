# P1-03 工具调用并行执行

- **对应问题**：#3 工具串行执行，无并行
- **提交**：`32a1f23 perf: 工具调用并行执行（asyncio.gather 替代串行 await）`
- **优先级**：P1（功能/体验差距）
- **状态**：已完成

---

## 1. 问题背景

优化前，Agent 在工具循环里对 LLM 返回的多个 `tool_calls` 逐个 `await`：

```python
for i, tool_call in enumerate(response.tool_calls or []):
    # 产出「执行中」UI
    ...
    result = await self.tool_registry.execute(tool_call, context)  # 串行阻塞
    ...
```

当 LLM 一次返回多个工具调用（例如同时查多张表、同时做多次检索）时，总延迟是各工具耗时之和：

```
T_total = T1 + T2 + T3 + ...
```

主流框架（OpenAI Agents SDK、LangGraph、Claude 的并行 tool use）都支持并行执行独立工具调用，把延迟降到 `max(T1, T2, T3, ...)`。

---

## 2. 主流方案参考

| 框架 | 并行策略 |
|---|---|
| OpenAI Agents SDK | 一次响应中的多个 tool call 并行执行 |
| LangGraph | 工具节点可并行分支，结果汇总 |
| Claude tool use | 客户端可并行执行多个 `tool_use` 块 |

前提：工具之间**相互独立、无共享可变状态**；有依赖关系的工具应由 LLM 分多轮调用。

---

## 3. 方案设计

采用「三段式」：

1. **阶段 1（串行）**：为每个 tool call 产出「执行中」UI（任务卡片 + 状态卡），并收集执行计划 `exec_plan`。
2. **阶段 2（并行）**：用 `asyncio.gather` 并发执行所有工具。
3. **阶段 3（串行）**：按原始 `tool_calls` 顺序产出「完成」UI 与结果。

这样既保留了 UI 的顺序与可读性，又让真正的耗时操作并行。

```
for each tool_call:
    yield 执行中 UI
    exec_plan.append((tool_call, task, status_card))

executed = await asyncio.gather(
    *(_execute_tool_call(tool_call, context) for tool_call, _, _ in exec_plan)
)

for (tool_call, task, status_card), result in zip(exec_plan, executed):
    yield 完成 UI + result
```

### 3.1 抽出 `_execute_tool_call`

把「取工具 → `before_tool` 钩子 → 执行（含埋点）→ `after_tool` 钩子」封装成一个返回 `ToolResult` 的协程，不产出 UI，便于 `gather`：

```python
async def _execute_tool_call(self, tool_call, context) -> ToolResult:
    tool = await self.tool_registry.get_tool(tool_call.name)
    if tool:
        for hook in self.lifecycle_hooks:
            await hook.before_tool(tool, context)
    result = await self.tool_registry.execute(tool_call, context)
    for hook in self.lifecycle_hooks:
        modified = await hook.after_tool(result)
        if modified is not None:
            result = modified
    return result
```

### 3.2 失败隔离

`ToolRegistry.execute` 内部已把异常兜底为 `ToolResult(success=False)`，因此 `asyncio.gather` 默认不会因单个工具异常而整体失败（除非自定义工具/钩子抛出未捕获异常）。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/agent/agent.py` | 工具循环改为三段式；新增 `_execute_tool_call`；`asyncio.gather` 并行执行 |
| `tests/test_token_streaming_e2e.py` | 新增「多工具并行」测试（用时间窗口重叠判断） |

### 关键代码

```python
# 阶段 2：并行执行所有工具
executed = await asyncio.gather(
    *(
        self._execute_tool_call(tool_call, context)
        for tool_call, _, _ in exec_plan
    )
)

# 阶段 3：按原顺序产出完成 UI 与结果
for (tool_call, tool_task, tool_status_card), result in zip(exec_plan, executed):
    ...
```

---

## 5. 使用方式

无需额外配置，默认启用。LLM 返回多个 `tool_calls` 时自动并行。

若某些工具**不能并发**（例如共享同一个数据库连接/文件句柄），可以：

1. 在工具内部自行加锁；
2. 或让 LLM 分多轮调用（工具描述中说明依赖关系）；
3. 或自定义 `ToolRegistry.execute` 做串行化控制。

---

## 6. 验证

`tests/test_token_streaming_e2e.py::test_multiple_tools_execute_in_parallel`：

- 两个慢工具（各 `sleep(0.1)`）；
- 断言两个工具的执行时间窗口存在重叠（`overlap > 0`）；
- 串行实现时重叠为负，并行实现时为正。

```python
overlap = min(tool_a.end, tool_b.end) - max(tool_a.start, tool_b.start)
assert overlap > 0
```

---

## 7. 兼容性与注意事项

1. **UI 顺序不变**：阶段 3 仍按 `tool_calls` 原始顺序产出结果，前端展示顺序稳定。
2. **钩子并发**：`before_tool` / `after_tool` 会在多个工具之间并发执行，钩子实现需线程/协程安全。
3. **审计**：审计日志在 `ToolRegistry.execute` 内按工具分别记录，顺序可能交错（并行导致），这是预期行为。
4. **资源竞争**：工具若共享连接/文件，需要自行加锁；框架不做全局串行化。
5. **超时**：单个工具的超时由其自身或 `RunSqlTool` 护栏控制；`gather` 不会自动取消其它工具。

---

## 8. 后续可扩展点

- 支持 `asyncio.gather(..., return_exceptions=True)` + 统一错误映射；
- 为工具增加 `parallel_safe: bool` 元数据，框架据此决定是否并行；
- 增加整体工具阶段超时（超时后取消剩余任务）。

---

## 附：提交与文件

- 提交：`32a1f23`
- 文件：`src/vanna/core/agent/agent.py`、`tests/test_token_streaming_e2e.py`
