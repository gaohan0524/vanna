# P1-18 接线 handle_tool_error（仅未捕获异常）

- **对应问题**：#18 `ErrorRecoveryStrategy.handle_tool_error` 定义了但从未被调用
- **优先级**：P1（扩展点语义/可靠性）
- **状态**：已完成
- **提交**：`045d8aa feat: 接线 handle_tool_error（仅未捕获异常，P1-18）`

---

## 1. 问题背景

`ErrorRecoveryStrategy` 提供两个钩子：

```python
async def handle_tool_error(self, error, context, attempt=1) -> RecoveryAction
async def handle_llm_error(self, error, request, attempt=1) -> RecoveryAction
```

其中 `handle_llm_error` 已在 LLM 调用层接线；`handle_tool_error` **从未被调用**。

而 `ToolRegistry.execute` 会把所有工具异常兜底为：

```python
ToolResult(success=False, result_for_llm="Execution failed: ...")
```

也就是说：

- 工具失败一律回喂 LLM，由模型自我修复（agentic 范式，对 SQL 语法错误很有效）；
- 但**基础设施级瞬时故障**（连接重置、网络超时等）也被当作普通失败，没有重试机会；
- `handle_tool_error` 成为一个「看起来支持、实际无效」的 API。

---

## 2. 方案设计

采用**窄接线**：只对「未捕获异常」触发的失败应用策略，业务错误仍走 LLM 自修复。

### 2.1 区分两类失败

| 失败类型 | 来源 | 是否重试 |
|---|---|---|
| 业务错误 | 工具自身返回 `ToolResult(success=False)`（如 SQL 语法错误、权限拒绝、参数校验失败） | **否**，回喂 LLM 自我修复 |
| 未捕获异常 | 工具执行抛出异常（连接重置、超时、第三方 SDK 异常等） | **是**，交给 `handle_tool_error` 决定 |

### 2.2 `ToolRegistry` 打标记

异常兜底时写入 `exception_type`：

```python
except Exception as e:
    msg = f"Execution failed: {str(e)}"
    return ToolResult(
        success=False,
        result_for_llm=msg,
        ui_component=None,
        error=msg,
        metadata={"exception_type": type(e).__name__},
    )
```

业务错误的 `metadata` 只有 `error_type`（如 `sql_error`），不会带 `exception_type`。

### 2.3 `_recover_tool_result`

在 `_execute_tool_call` 中，工具执行后立即调用：

```python
result = await self.tool_registry.execute(tool_call, context)
result = await self._recover_tool_result(tool_call, context, result)
```

策略动作映射：

| 动作 | 行为 |
|---|---|
| `RETRY` | 按 `retry_delay_ms` 退避后重新执行；最多 `MAX_TOOL_RECOVERY_ATTEMPTS = 3` 次 |
| `FALLBACK` | 若 `fallback_value` 是 `ToolResult`，直接采用 |
| `SKIP` | 返回结构化的失败结果（`error_type="tool_skipped"`）回喂 LLM |
| `FAIL` / 未知 | 保留原失败结果，交给 LLM 自我修复（默认行为不变） |

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/registry.py` | 异常兜底结果增加 `metadata={"exception_type": ...}` |
| `src/vanna/core/agent/agent.py` | 新增 `MAX_TOOL_RECOVERY_ATTEMPTS = 3` 与 `_recover_tool_result`；`_execute_tool_call` 接入 |
| `src/vanna/core/recovery/base.py` | 更新接线说明（handle_tool_error 仅对未捕获异常生效） |
| `src/vanna/core/recovery/models.py` | 同上 |
| `src/vanna/examples/extensibility_example.py` | 补充策略适用范围说明 |
| `tests/test_tool_error_recovery.py` | 新增 6 个用例 |

### 关键代码

```python
async def _recover_tool_result(self, tool_call, context, result):
    if result.success or not self.error_recovery_strategy:
        return result
    if not (result.metadata or {}).get("exception_type"):
        return result  # 业务错误：交给 LLM 自我修复

    attempt = 1
    while attempt <= MAX_TOOL_RECOVERY_ATTEMPTS:
        action = await self.error_recovery_strategy.handle_tool_error(
            RuntimeError(result.error or "Tool execution failed"), context, attempt
        )
        if action.action == RecoveryActionType.RETRY:
            if action.retry_delay_ms:
                await asyncio.sleep(action.retry_delay_ms / 1000)
            attempt += 1
            result = await self.tool_registry.execute(tool_call, context)
            if result.success:
                return result
            if not (result.metadata or {}).get("exception_type"):
                return result
            continue
        if action.action == RecoveryActionType.FALLBACK:
            return action.fallback_value if isinstance(action.fallback_value, ToolResult) else result
        if action.action == RecoveryActionType.SKIP:
            return ToolResult(..., metadata={"error_type": "tool_skipped"})
        return result
    return result
```

---

## 4. 使用方式

不配置策略时行为完全不变（默认 FAIL → LLM 自修复）。需要重试基础设施故障时：

```python
from vanna.core.recovery import (
    ErrorRecoveryStrategy, RecoveryAction, RecoveryActionType,
)

class RetryTransientToolErrors(ErrorRecoveryStrategy):
    async def handle_tool_error(self, error, context, attempt=1):
        if attempt < 3:
            return RecoveryAction(
                action=RecoveryActionType.RETRY,
                retry_delay_ms=(2 ** (attempt - 1)) * 500,
            )
        return RecoveryAction(action=RecoveryActionType.FAIL, message=str(error))

agent = Agent(
    ...,
    error_recovery_strategy=RetryTransientToolErrors(),
)
```

---

## 5. 验证

`tests/test_tool_error_recovery.py`（6 passed）：

- 未捕获异常 + RETRY：工具被重试，第二次成功；
- 未配置策略：不重试，失败结果回喂 LLM；
- 业务错误 + AlwaysRetry 策略：**不重试**（`exception_type` 缺失）；
- FALLBACK：使用 `ToolResult` 作为替代结果；
- SKIP：返回 `tool_skipped` 结构化失败；
- 重试上限：初始 1 次 + 最多 3 次重试。

回归：相关测试集 **179 passed, 3 skipped**。

---

## 6. 兼容性与注意事项

1. **默认行为不变**：默认策略返回 FAIL，工具失败仍回喂 LLM。
2. **业务错误不重试**：避免 SQL 语法错误与 LLM 自修复形成双重循环。
3. **重试上限**：`MAX_TOOL_RECOVERY_ATTEMPTS = 3`，防止策略实现错误导致无限重试。
4. **FALLBACK 类型**：只有 `RecoveryAction.fallback_value` 为 `ToolResult` 时才会采用；其它类型保留原结果。
5. **并行工具**：重试发生在单个 `_execute_tool_call` 内部，不影响其它并行工具。
6. **超时**：重试会重新执行工具；若工具是 SQL，建议同时配置 `RunSqlTool.timeout_seconds`，避免单次重试耗时过长。

---

## 7. 后续可扩展点

- 按异常类型细分策略（超时 vs 连接重置 vs 限流）；
- 将重试次数/最终结果写入审计与可观测性；
- 为 `ToolResult` 增加 `retryable` 标记，由工具自己声明是否可重试；
- 与 P1-06 SQL 护栏联动：超时错误可返回更明确的 `query_timeout` 供策略判断。

---

## 附：文件

- 修改：`src/vanna/core/registry.py`、`src/vanna/core/agent/agent.py`、`src/vanna/core/recovery/base.py`、`src/vanna/core/recovery/models.py`、`src/vanna/examples/extensibility_example.py`
- 测试：`tests/test_tool_error_recovery.py`
