# P1-19 core/errors.py 去留与异常语义

- **对应问题**：#19 `core/errors.py` 定义了整套异常类但基本未接线，且存在命名冲突
- **优先级**：P1（清账/语义）
- **状态**：已完成
- **提交**：`03242de feat: 明确异常语义并实现 thinking 指示（P1-19/P1-20）`

---

## 1. 问题背景

`core/errors.py` 定义了 7 个异常类，但优化前：

- 核心执行路径不抛它们：`Agent._send_message` / `ToolRegistry.execute` 用
  `ToolResult(success=False, error=...)` 传递工具失败，由 LLM 自我修复；
- 只有 `quota_lifecycle_example.py` 自定义了一个 `QuotaExceededError(AgentError)`；
- `PermissionError` / `ValidationError` 与 Python 内置 / Pydantic 同名，容易误用；
- 注释里多处标着【待确认设计】，属于「看起来支持、实际未接线」的 API。

---

## 2. 去留决策

| 异常类 | 决策 | 说明 |
|---|---|---|
| `AgentError` | **保留** | 统一基类，供 `except AgentError` 一次性兜底 |
| `LlmServiceError` | **保留 + 接线** | LLM 恢复失败（FAIL / 重试上限 / 非法 FALLBACK）时由 Agent 抛出，保留原始异常为 `__cause__` |
| `ConversationNotFoundError` | **保留（扩展点语义）** | 自定义 `ConversationStore` 在会话缺失时可显式抛出 |
| `ToolExecutionError` | **保留（扩展点语义）** | 工具作者需要显式中断时使用；核心仍返回 `ToolResult(success=False)` |
| `ToolNotFoundError` | **保留（扩展点语义）** | 核心返回失败结果让 LLM 纠正工具名，不抛异常 |
| `PermissionError` | **保留（扩展点语义）** | 仅用于业务权限；系统文件权限仍用内置 `PermissionError` |
| `ValidationError` | **保留（扩展点语义）** | 仅用于业务校验；Pydantic 字段校验仍用 `pydantic.ValidationError` |

关键原则：**不为了「用上异常」而改变 agentic 错误处理范式**。工具错误继续回喂 LLM；只把 LLM 通信失败的终态显式化为 `LlmServiceError`。

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/errors.py` | 重写模块/类 docstring：去掉【待确认设计】，明确每个类的使用场景与命名冲突 |
| `src/vanna/core/agent/agent.py` | 引入 `LlmServiceError`；`_invoke_llm_with_recovery` 的 FAIL / 重试上限 / 非法 FALLBACK 改为 `raise LlmServiceError(...) from e` |
| `tests/test_error_recovery.py` | `test_fail_propagates` / `test_retry_limit_exceeded` 改为断言 `LlmServiceError` |
| `tests/test_core_errors.py` | 新增 3 个用例：继承关系、FAIL 包装 cause、重试上限包装 cause |

### 关键代码

```python
# agent.py::_invoke_llm_with_recovery
if attempt > MAX_LLM_RECOVERY_ATTEMPTS:
    raise LlmServiceError(
        f"LLM recovery attempt limit ({MAX_LLM_RECOVERY_ATTEMPTS}) exceeded: {e}"
    ) from e
...
if action.action == RecoveryActionType.FALLBACK and not isinstance(
    action.fallback_value, LlmResponse
):
    raise LlmServiceError(
        f"FALLBACK action without LlmResponse fallback_value: {e}"
    ) from e
...
# FAIL 或未知动作
raise LlmServiceError(f"LLM request failed: {e}") from e
```

---

## 4. 行为变化

| 场景 | 优化前 | 优化后 |
|---|---|---|
| LLM 调用失败且策略返回 FAIL | 原异常（如 `RuntimeError`）向上抛 | `LlmServiceError`，`__cause__` 指向原异常 |
| 重试超过防御上限 | 原异常向上抛 | `LlmServiceError`，消息含 "attempt limit" |
| FALLBACK 但 fallback_value 非法 | 原异常向上抛 | `LlmServiceError` |
| 工具执行失败 | `ToolResult(success=False)` | **不变** |
| 工具执行未捕获异常 | `ToolResult(success=False)` | **不变**（可配合 P1-18 重试） |

`send_message` 的 `except Exception` 会捕获 `LlmServiceError` 并展示错误卡片，消息文本会带上 `"LLM request failed: ..."` 前缀。

---

## 5. 使用方式

```python
from vanna.core.errors import AgentError, LlmServiceError

try:
    async for component in agent.send_message(...):
        ...
except LlmServiceError as e:
    # 模型服务不可用（重试/降级都失败）
    log.error("LLM unavailable: %s", e, exc_info=e.__cause__)
except AgentError as e:
    # 其它框架级错误
    ...
```

自定义扩展点可直接复用基类：

```python
from vanna.core.errors import AgentError

class QuotaExceededError(AgentError):
    pass
```

---

## 6. 验证

- `tests/test_core_errors.py`（3 passed）
  - 6 个子类均继承 `AgentError`；
  - FAIL 时抛 `LlmServiceError`，`__cause__` 为原始 `RuntimeError`；
  - 重试上限时抛 `LlmServiceError`，消息含原始错误文本。
- `tests/test_error_recovery.py` 更新后全部通过。
- 全量相关回归：**184 passed, 3 skipped**。

---

## 7. 兼容性与注意事项

1. **`LlmServiceError` 是 `Exception` 子类**：既有的 `except Exception` 不受影响。
2. **捕获原始异常类型的地方需调整**：若调用方原本 `except RuntimeError`，现在需要
   `except LlmServiceError` 或查看 `__cause__`。
3. **工具错误语义不变**：仍然是 `ToolResult(success=False)` 回喂 LLM。
4. **命名冲突仍在**：`PermissionError` / `ValidationError` 为了兼容未改名；使用时
   请显式 `from vanna.core.errors import ...`，避免遮蔽内置/Pydantic 异常。
5. **未配置恢复策略时**：`_invoke_llm_with_recovery` 直通，仍抛原始异常（不包装），
   保持零开销与最小惊讶。

---

## 8. 后续可扩展点

- 将 `PermissionError` / `ValidationError` 重命名为 `AgentPermissionError` /
  `AgentValidationError`，旧名保留为弃用别名；
- 为 `LlmServiceError` 增加 `provider` / `status_code` 等结构化字段；
- 在可观测性中把 `LlmServiceError` 记为独立错误类型。

---

## 附：文件

- 修改：`src/vanna/core/errors.py`、`src/vanna/core/agent/agent.py`
- 测试：`tests/test_core_errors.py`、`tests/test_error_recovery.py`
