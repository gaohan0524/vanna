# P1-08 内置上下文窗口管理（滑动窗口 + 总结压缩）

- **对应问题**：#8 无内置上下文窗口管理
- **提交**：`23b5432 feat: 内置上下文窗口管理（滑动窗口 + 总结压缩）`
- **优先级**：P1（功能/体验差距）
- **状态**：已完成

---

## 1. 问题背景

`conversation_filters` 是框架的扩展点，但**没有内置实现**。长对话会不断累积历史消息，最终：

- 超出模型上下文窗口，请求报错；
- 或 token 成本随对话轮次线性增长；
- 使用者必须自己写一个 `ConversationFilter`。

主流框架（LangChain、LlamaIndex 等）都内置了多种上下文窗口管理策略。

---

## 2. 主流方案参考

| 方案 | 代表 | 特点 |
|---|---|---|
| 滑动窗口 | LangChain `ConversationBufferWindowMemory` | 只保留最近 N 条/token，零 LLM 成本，最旧信息直接丢弃 |
| 全量总结 | LangChain `ConversationSummaryMemory` | 整段历史压缩成摘要，省 token 但丢近期细节 |
| 混合 | LangChain `ConversationSummaryBufferMemory`、Claude Code compaction | 最近一段原文 + 更早部分滚动摘要，兼顾细节与上限 |

核心要点：

1. 按 **token 预算**裁剪（而非固定条数）；
2. 裁剪时保持 **消息协议完整性**（`tool` 结果不能与它的 `assistant(tool_calls)` 拆开）；
3. 混合方案需要 **增量摘要**，避免每次请求都重算全部历史。

---

## 3. 方案设计

新增三个组件：

```
core/filter/
├── token_utils.py        # token 估算 + 窗口起点计算
├── sliding_window.py     # SlidingWindowFilter
└── summary.py            # SummaryBufferFilter
```

### 3.1 token 估算

默认用轻量启发式（不引入 tiktoken 依赖）：

```python
def estimate_tokens(text, tokenizer=None):
    if tokenizer is not None:
        return int(tokenizer(text))
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii)
```

- ASCII 约 4 字符/token；
- 非 ASCII（CJK）约 1 字符/token；
- 支持注入精确 tokenizer（如 tiktoken 的 `encode`）。

### 3.2 窗口起点计算

```python
def compute_window_start(messages, max_tokens, tokenizer=None) -> int:
    total = 0
    start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        total += estimate_message_tokens(messages[i], tokenizer)
        if total > max_tokens and i < len(messages) - 1:
            start = i + 1
            break
        start = i

    # 协议完整性：窗口起点不能是孤立的 tool 消息
    while start < len(messages) and messages[start].role == "tool":
        start -= 1
    return max(start, 0)
```

两点保证：

1. 至少保留最后一条消息（即使单条超预算）；
2. 窗口起点不会落在孤立的 `tool` 消息上——会向前补回其所属的 `assistant(tool_calls)`。

### 3.3 滑动窗口

```python
class SlidingWindowFilter(ConversationFilter):
    def __init__(self, max_tokens=8000, tokenizer=None, keep_system_messages=True):
        ...

    async def filter_messages(self, messages):
        system_msgs = [m for m in messages if m.role == "system"]
        others = [m for m in messages if m.role != "system"]
        start = compute_window_start(others, self.max_tokens, self.tokenizer)
        return system_msgs + others[start:]
```

### 3.4 总结压缩（混合策略）

```python
class SummaryBufferFilter(ConversationFilter):
    def __init__(
        self,
        llm_service: LlmService,
        max_tokens=4000,
        tokenizer=None,
        summary_system_prompt=None,
        summary_prefix="[以下为之前对话的摘要，请结合它回答]\n",
        summary_role="user",
        fallback_on_error=True,
    ):
        ...

    async def filter_messages(self, messages):
        start = compute_window_start(messages, self.max_tokens, self.tokenizer)
        recent, old = messages[start:], messages[:start]
        if not old:
            return recent
        summary = await self._update_summary(messages, old)
        return [Message(role=self.summary_role, content=self.summary_prefix + summary)] + recent
```

**增量滚动摘要**：

- 用「会话首条消息（role + content + timestamp）」作为稳定的 `conversation_key`；
- 记录 `(已摘要消息数, 摘要文本)`；
- 每次只把「新被裁剪掉」的消息与既有摘要一起压缩，摘要体积保持有界；
- LLM 摘要失败时（`fallback_on_error=True`）降级为朴素截断，保证请求不中断。

**摘要消息角色**：默认 `role="user"`，避免与框架已有的 `system_prompt` 形成两条 system 消息；可通过 `summary_role` 改为 `system` 等。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/filter/token_utils.py` | 新增：`estimate_tokens`、`estimate_message_tokens`、`compute_window_start` |
| `src/vanna/core/filter/sliding_window.py` | 新增：`SlidingWindowFilter` |
| `src/vanna/core/filter/summary.py` | 新增：`SummaryBufferFilter`（滚动摘要 + 兜底） |
| `src/vanna/core/filter/__init__.py` | 导出新过滤器 |
| `src/vanna/core/__init__.py` | 导出 `SlidingWindowFilter` / `SummaryBufferFilter` |
| `src/vanna/__init__.py` | 顶层导出 |
| `tests/test_context_management.py` | 9 个用例 |

---

## 5. 使用方式

```python
from vanna import Agent, SlidingWindowFilter, SummaryBufferFilter

agent = Agent(
    ...,
    conversation_filters=[
        # 方案 A：纯滑动窗口（零 LLM 成本）
        SlidingWindowFilter(max_tokens=8000),

        # 方案 B：混合总结压缩（需要同一个 llm_service）
        SummaryBufferFilter(llm_service=llm, max_tokens=4000),
    ],
)
```

> 多个过滤器按顺序串联：前一个的输出是后一个的输入。通常只选一种，或先做脱敏再做窗口裁剪。

精确计数（可选）：

```python
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")
filter = SlidingWindowFilter(max_tokens=8000, tokenizer=lambda t: len(enc.encode(t)))
```

---

## 6. 验证

`tests/test_context_management.py`（9 passed）：

- token 估算启发式 / 精确 tokenizer；
- 滑动窗口裁剪最旧消息、保留 system 消息、协议完整性（不产生孤立 tool）；
- 至少保留一条消息；
- 总结压缩：旧消息折叠为摘要 + 保留近期窗口；
- 增量摘要：第二次调用只压缩新被裁剪的消息，并合并既有摘要；
- 预算内直通（不调用 LLM）；
- LLM 摘要失败时兜底。

---

## 7. 兼容性与注意事项

1. **默认不启用**：不传 `conversation_filters` 时行为不变。
2. **过滤器只影响发往 LLM 的历史**，不修改存储中的完整会话。
3. **摘要状态是进程内内存**：`SummaryBufferFilter._state` 按会话 key 缓存；多实例部署时各实例独立，可由 `conversation_key` 重新计算（不共享）。
4. **摘要角色**：默认 `user`；若你的模型/供应商对多条 system 消息不敏感，可改为 `system`。
5. **协议完整性**：滑动窗口只保证不产生孤立 `tool` 消息；若业务还有更强的消息配对约束，需要自定义过滤器。
6. **估算误差**：默认启发式是近似值，预算应留一定余量；需要精确控制时注入 tokenizer。

---

## 8. 后续可扩展点

- 摘要状态持久化（写入会话 metadata / 外部存储），支持多实例共享；
- 增加按「对话轮次」而非 token 的窗口策略；
- 增加「重要消息保留」规则（如包含 SQL 的轮次优先保留）；
- 接入 tiktoken 作为默认精确计数器（可选 extra）。

---

## 附：提交与文件

- 提交：`23b5432`
- 文件：`src/vanna/core/filter/token_utils.py`、`sliding_window.py`、`summary.py`
- 测试：`tests/test_context_management.py`
