# P1-20 实现 include_thinking_indicators

- **对应问题**：#20 `include_thinking_indicators` 仅有 TODO 占位，开关无效
- **优先级**：P1（体验/清账）
- **状态**：已完成
- **提交**：`03242de feat: 明确异常语义并实现 thinking 指示（P1-19/P1-20）`

---

## 1. 问题背景

`AgentConfig.include_thinking_indicators` 默认 `True`，但 agent.py 中的实现是：

```python
if self.config.include_thinking_indicators and tool_iterations == 0:
    # TODO: Yield thinking indicator
    pass
```

即：配置项存在、默认开启，但**没有任何效果**。使用者以为开启后会看到「思考中」提示，实际不会。

---

## 2. 方案设计

### 2.1 输出「思考中」状态栏

在工具循环第一轮、调用 LLM 之前，输出一次状态栏更新：

```python
if self.config.include_thinking_indicators and tool_iterations == 0:
    yield UiComponent(
        rich_component=StatusBarUpdateComponent(
            status="working",
            message="Thinking...",
            detail="Reasoning about your request",
        )
    )
```

选择「状态栏更新」而不是新增卡片，原因：

- 避免 UI 噪音（不产生需要清理的持久卡片）；
- 与已有的 `"Processing your request..."` 状态自然衔接；
- 后续轮次会由 `"Executing tools..."` 状态接管。

### 2.2 保留开关

- `True`（默认）：首轮输出 `Thinking...`；
- `False`：不输出，保持原行为。

### 2.3 更新配置注释

去掉「目前仅为 TODO 占位，未实现」的描述，改为实际语义。

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/agent/agent.py` | 将 TODO/pass 替换为 `StatusBarUpdateComponent(message="Thinking...")` |
| `src/vanna/core/agent/config.py` | 更新 `include_thinking_indicators` 注释 |
| `tests/test_thinking_indicator.py` | 新增 2 个用例：开启/关闭 |

### 关键代码

```python
if self.config.include_thinking_indicators and tool_iterations == 0:
    # 首次进入工具循环时给出「思考中」状态；后续轮次由 "Executing tools..." 接管。
    yield UiComponent(
        rich_component=StatusBarUpdateComponent(
            status="working",
            message="Thinking...",
            detail="Reasoning about your request",
        )
    )
```

---

## 4. 使用方式

默认生效，无需配置：

```python
agent = Agent(...)
```

如需关闭：

```python
agent = Agent(
    ...,
    config=AgentConfig(include_thinking_indicators=False),
)
```

前端会收到一个 `status_bar_update` 组件：

```json
{
  "type": "status_bar_update",
  "data": {
    "status": "working",
    "message": "Thinking...",
    "detail": "Reasoning about your request"
  }
}
```

---

## 5. 验证

`tests/test_thinking_indicator.py`（2 passed）：

- 开启时，组件流中出现 `"Thinking..."` 状态；
- 关闭时，不出现 `"Thinking..."`。

全量相关回归：**184 passed, 3 skipped**。

---

## 6. 兼容性与注意事项

1. **默认行为变化**：默认 `True`，因此现在会多出一条状态栏更新（此前无任何输出）。
   如不希望出现，显式设置 `include_thinking_indicators=False`。
2. **仅状态栏**：不新增卡片、不改变组件树结构，前端无需适配新组件类型。
3. **首轮限定**：只在 `tool_iterations == 0` 输出一次，避免每轮重复。
4. **与流式的关系**：状态栏先于首个 token 输出，随后由 token 级文本组件接管。

---

## 7. 后续可扩展点

- 支持更丰富的 thinking 卡片（如 `StatusCardComponent` + 生命周期移除）；
- 对支持 reasoning 的模型，展示推理摘要（需供应商返回 reasoning tokens）；
- 将 thinking 阶段耗时记录到可观测性。

---

## 附：文件

- 修改：`src/vanna/core/agent/agent.py`、`src/vanna/core/agent/config.py`
- 测试：`tests/test_thinking_indicator.py`
