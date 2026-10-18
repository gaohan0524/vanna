# P1-14 UiFeatures 默认分组浅拷贝共享

- **对应问题**：#14 `UiFeatures.feature_group_access` 默认值只做了 dict 浅拷贝，内部 list 仍被所有实例共享
- **关联问题**：P1-12（`Agent.__init__` 共享可变默认参数）的同源问题
- **优先级**：P1（正确性隐患）
- **状态**：已完成
- **提交**：`6ecca2b fix: UiFeatures 默认分组按实例深拷贝（P1-14）`

---

## 1. 问题背景

`UiFeatures.feature_group_access` 的默认值来自全局常量 `DEFAULT_UI_FEATURES`：

```python
DEFAULT_UI_FEATURES: Dict[str, List[str]] = {
    UiFeature.UI_FEATURE_SHOW_TOOL_NAMES: ["admin", "user"],
    UiFeature.UI_FEATURE_SHOW_TOOL_ARGUMENTS: ["admin"],
    ...
}

class UiFeatures(BaseModel):
    feature_group_access: Dict[str, List[str]] = Field(
        default_factory=lambda: DEFAULT_UI_FEATURES.copy(),   # 浅拷贝
        ...
    )
```

`dict.copy()` 只复制外层字典，**内部 list 仍是同一批对象**。因此每个 `UiFeatures` / `AgentConfig` 实例虽然拥有独立的 dict，但取值列表是共享的。

实测复现：

```python
a, b = AgentConfig(), AgentConfig()
a.ui_features.feature_group_access["tool_arguments"].append("__probe__")
print(b.ui_features.feature_group_access["tool_arguments"])
# ['admin', '__probe__']   ← b 被污染
```

### 影响

- 任何对某个 Agent 的 UI 权限列表做增删，都会污染**其它所有** Agent 的默认权限；
- 尤其危险的是 `tool_arguments`（SQL 语句默认仅 admin 可见）：如果某处给 A 加了 `"user"`，所有实例的普通用户都会看到 SQL；
- 与 P1-12 属于同一类「共享可变默认值」问题，只是藏在嵌套模型里。

---

## 2. 方案设计

把浅拷贝改为**逐项拷贝内部 list**：

```python
feature_group_access: Dict[str, List[str]] = Field(
    default_factory=lambda: {
        feature: list(groups) for feature, groups in DEFAULT_UI_FEATURES.items()
    },
    ...
)
```

要点：

- 外层 dict 与内层 list 都是新对象，实例之间完全隔离；
- 默认值内容不变（仍是 admin/user 等分组）；
- `register_feature()` 写入的是实例自己的 dict，不会影响全局 `DEFAULT_UI_FEATURES`，也不会影响其它实例。

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/core/agent/config.py` | `UiFeatures.feature_group_access` 的 `default_factory` 改为逐项 `list(...)` 拷贝 |
| `tests/test_agent_defaults.py` | 新增 2 个用例：默认 list 隔离、`register_feature` 隔离 |

### 关键代码

```python
# 优化前
default_factory=lambda: DEFAULT_UI_FEATURES.copy()

# 优化后
default_factory=lambda: {
    feature: list(groups) for feature, groups in DEFAULT_UI_FEATURES.items()
}
```

---

## 4. 使用方式

对外 API 无变化：

```python
cfg = AgentConfig()
cfg.ui_features.register_feature("my_feature", ["admin"])
cfg.ui_features.can_user_access_feature("my_feature", admin_user)
```

每个 `AgentConfig()` 现在拥有完全独立的权限表。

---

## 5. 验证

`tests/test_agent_defaults.py`（7 passed）新增：

- `test_ui_features_default_lists_are_not_shared`：A 往 `tool_arguments` 追加后，B 仍为 `["admin"]`；
- `test_ui_features_register_feature_is_isolated`：A 注册自定义功能不影响 B。

回归：相关测试集 **168 passed, 3 skipped**（含 `test_tool_permissions`、`test_memory_tools` 等权限相关用例）。

---

## 6. 兼容性与注意事项

1. **默认值不变**：`tool_names` 仍对 `admin/user` 可见，`tool_arguments`/`tool_error` 等仍仅 `admin`。
2. **行为变化**：修改某个实例的权限列表不再影响其它实例（这正是修复目标）。
3. **共享配置的显式用法**：如果确实想让多个 Agent 共享同一份 `UiFeatures`，显式传入同一个 `AgentConfig` 即可（框架保留引用）。
4. **不可变方案（可选）**：也可把类型改为 `Dict[str, Tuple[str, ...]]` 或使用 `frozenset`，从类型层面禁止误改；本次保持 `List[str]` 以减少 API 变更。

---

## 7. 后续可扩展点

- 将 `feature_group_access` 改为不可变结构（`Mapping[str, FrozenSet[str]]`）；
- 为 `UiFeatures` 增加 `model_copy(deep=True)` 的便捷方法；
- 在 `AgentConfig` 层统一做 `model_copy(deep=True)` 的克隆入口，避免类似嵌套共享。

---

## 附：文件

- 修改：`src/vanna/core/agent/config.py`
- 测试：`tests/test_agent_defaults.py`
