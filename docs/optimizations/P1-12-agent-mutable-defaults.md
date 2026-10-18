# P1-12 Agent 共享可变默认参数

- **对应问题**：#12 `Agent.__init__` 使用可变默认参数，多个实例共享同一份状态
- **优先级**：P1（正确性隐患）
- **状态**：已完成
- **提交**：`f0dadfd feat: 修复 Agent 共享可变默认参数（P1-12）`

---

## 1. 问题背景

优化前，`Agent.__init__` 的签名（代码注释已标记【待确认设计】）：

```python
def __init__(
    self,
    ...,
    config: AgentConfig = AgentConfig(),
    system_prompt_builder: SystemPromptBuilder = DefaultSystemPromptBuilder(),
    lifecycle_hooks: List[LifecycleHook] = [],
    llm_middlewares: List[LlmMiddleware] = [],
    context_enrichers: List[ToolContextEnricher] = [],
    conversation_filters: List[ConversationFilter] = [],
    ...
):
```

Python 的默认参数只在**函数定义时求值一次**，因此：

- `AgentConfig()` 只创建一次，所有未显式传 `config` 的 Agent **共享同一个 Pydantic 实例**；
- `DefaultSystemPromptBuilder()` 同理；
- 四个空列表也是同一个 list 对象。

一旦有人对共享对象做写操作，就会污染其它实例：

```python
agent_a = Agent(...)   # 未传 config
agent_b = Agent(...)   # 未传 config
agent_a.config.temperature = 0.1
print(agent_b.config.temperature)   # 0.1 —— 被串改
```

同理，`agent_a.lifecycle_hooks.append(hook)` 会让所有默认构造的 Agent 都多出这个 hook。

这是典型的「跨实例状态串扰」问题：当前框架内部只读不写，所以没有立刻爆炸；但只要使用者按直觉修改 `agent.config` 或往 hooks 里追加，就会踩坑。

---

## 2. 根因与主流做法

| 语言/框架 | 做法 |
|---|---|
| Python 官方 | 可变默认参数改用 `None` 占位，函数体内初始化 |
| Pydantic | 模型字段用 `Field(default_factory=...)` |
| 主流 Agent 框架 | 配置对象按实例创建；hook/middleware 列表在构造时复制或新建 |

核心原则：**默认值不能是可变对象；实例状态必须按实例隔离。**

---

## 3. 方案设计

### 3.1 签名改为 `Optional[...] = None`

```python
config: Optional[AgentConfig] = None,
system_prompt_builder: Optional[SystemPromptBuilder] = None,
lifecycle_hooks: Optional[List[LifecycleHook]] = None,
llm_middlewares: Optional[List[LlmMiddleware]] = None,
context_enrichers: Optional[List[ToolContextEnricher]] = None,
conversation_filters: Optional[List[ConversationFilter]] = None,
```

### 3.2 函数体内按实例初始化

```python
self.config = config if config is not None else AgentConfig()
self.system_prompt_builder = (
    system_prompt_builder
    if system_prompt_builder is not None
    else DefaultSystemPromptBuilder()
)

# 防御性拷贝：即使外部把同一个 list 传给多个 Agent，也各自持有独立副本
self.lifecycle_hooks = list(lifecycle_hooks or [])
self.llm_middlewares = list(llm_middlewares or [])
self.context_enrichers = list(context_enrichers or [])
self.conversation_filters = list(conversation_filters or [])
```

设计取舍：

- **`config` / `system_prompt_builder`**：未传时新建；显式传入时**保留引用**（允许用户有意共享同一份配置，例如统一的配额配置）。
- **列表类**：一律 `list(...)` 拷贝。即使两个 Agent 被传入同一个 list，也各自独立，避免隐式耦合。
- 其它可选扩展点（`workflow_handler` / `error_recovery_strategy` / `llm_context_enhancer` / `observability_provider` / `audit_logger`）原本就是 `None` 默认，无需改动。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/agent/agent.py` | `__init__` 签名改为 `Optional[...] = None`；函数体内按实例初始化 config / system_prompt_builder / 四个列表；移除【待确认设计】注释 |
| `tests/test_agent_defaults.py` | 新增 5 个回归测试，验证默认状态隔离 |

### 关键代码

```python
# 每个 Agent 实例独立初始化，避免共享默认实例
self.config = config if config is not None else AgentConfig()
self.system_prompt_builder = (
    system_prompt_builder
    if system_prompt_builder is not None
    else DefaultSystemPromptBuilder()
)

# 防御性拷贝
self.lifecycle_hooks = list(lifecycle_hooks or [])
self.llm_middlewares = list(llm_middlewares or [])
...
self.context_enrichers = list(context_enrichers or [])
...
self.conversation_filters = list(conversation_filters or [])
```

---

## 5. 使用方式与行为变化

### 5.1 调用方式不变

所有参数仍是可选关键字参数，位置参数顺序未变：

```python
agent = Agent(
    llm_service=...,
    tool_registry=...,
    user_resolver=...,
    agent_memory=...,
    # 以下均可省略
    # config=AgentConfig(temperature=0.2),
    # lifecycle_hooks=[QuotaHook()],
)
```

### 5.2 行为变化

| 场景 | 优化前 | 优化后 |
|---|---|---|
| 两个默认 Agent 修改 `config` | 互相影响 | 各自独立 |
| 两个默认 Agent 往 hooks 追加 | 互相影响 | 各自独立 |
| 显式传入 `config` | 使用该实例 | 仍使用该实例（引用不变） |
| 显式传入 hooks list 后再修改原 list | 影响 Agent | **不影响**（已拷贝） |

> 唯一需要注意的兼容点：如果你依赖「构造后继续修改传入的 list，让 Agent 感知」这种用法，需要改为直接操作 `agent.lifecycle_hooks`。

---

## 6. 验证

新增 `tests/test_agent_defaults.py`（5 passed）：

- `test_default_config_is_not_shared`：两个默认 Agent 的 `config` 不是同一对象，修改互不影响；
- `test_default_lists_are_not_shared`：四个列表互不共享；
- `test_default_system_prompt_builder_is_not_shared`：默认 builder 不是同一对象；
- `test_explicit_config_is_preserved`：显式传入的 config 引用保持不变；
- `test_explicit_lists_are_copied`：传入的 list 被防御性拷贝，外部修改不影响 Agent。

回归：相关测试集 **161 passed, 3 skipped**。

---

## 7. 兼容性与注意事项

1. **API 兼容**：参数名、顺序、可选性均未变；仅默认值从「共享实例」变为「按实例创建」。
2. **显式共享仍是可能的**：如果确实希望多个 Agent 共享同一份配置，显式传入同一个 `config` 即可（框架保留引用）。
3. **列表拷贝语义**：传入的 hooks/middlewares 等 list 会被拷贝；构造后修改原 list 不再影响 Agent。
4. **类型检查**：`self.config` 等属性在初始化后均为非 Optional 类型，mypy 可正常推断。

---

## 8. 后续可扩展点

- 为 `AgentConfig` 增加 `model_copy()` 友好的 `with_overrides()` 便捷方法；
- 在 `AgentConfig` 中把 `ui_features` / `audit_config` 等嵌套模型统一用 `default_factory`（当前已满足）；
- 增加「配置快照」日志，便于排查运行时配置差异。

---

## 附：文件

- 修改：`src/vanna/core/agent/agent.py`
- 测试：`tests/test_agent_defaults.py`
