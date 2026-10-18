# P1-21 RunSqlTool 安全默认值

- **对应问题**：`RunSqlTool` 默认 `read_only=False`、`max_rows=None`，开箱即写且大结果集可打满内存/SSE
- **优先级**：P1（安全/DoS 防护）
- **状态**：已完成
- **提交**：`3c4c958 feat: 收紧 SQL 默认值并修复公开 API（P1-21/22/23）`

---

## 1. 问题背景

`RunSqlTool` 的护栏此前都是「opt-in」：

```python
read_only: bool = False,          # 默认可写
max_rows: Optional[int] = None,   # 默认不限行
```

后果：

- 未显式配置时，LLM 生成的 `DELETE`/`DROP` 可直接执行；
- `SELECT * FROM 大表` 会把整份结果集同时转成 `records` + CSV 字符串 + DataFrame 组件，并整包推进 SSE，单请求即可打满内存/带宽。

企业级 Text2SQL 的共识是「安全默认」：默认只读、默认限制返回行数，需要放宽时显式开启。

---

## 2. 方案设计

把默认值翻转：

```python
read_only: bool = True,            # 默认只读，确需写入显式置 False
max_rows: Optional[int] = 1000,    # 默认最多返回 1000 行，None 表示不限
```

- `max_rows` 仍保持 `Optional[int]` 类型，显式传 `None` 依然表示「不限」，仅默认值变为 1000；
- 同时更新 docstring 说明「安全优先」的取舍。

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `tools/run_sql.py` | `read_only` 默认 `False→True`、`max_rows` 默认 `None→1000`，更新 docstring |
| `tests/test_public_api_defaults.py` | 新增 `test_run_sql_secure_defaults` |

### 关键代码

```python
def __init__(
    self,
    sql_runner: SqlRunner,
    ...,
    read_only: bool = True,            # 默认只读
    max_rows: Optional[int] = 1000,     # 默认限 1000 行
    ...
):
```

---

## 4. 使用方式

```python
# 默认：只读 + 限 1000 行（推荐）
db_tool = RunSqlTool(sql_runner=SqliteRunner("vanna.db"))

# 确需写入 / 不限行时显式放开
db_tool = RunSqlTool(sql_runner=..., read_only=False, max_rows=None)
```

---

## 5. 验证

- `tests/test_public_api_defaults.py::test_run_sql_secure_defaults`：默认 `read_only is True`、`max_rows == 1000`。
- 回归：核心测试集 **174 passed, 3 skipped**（既有 `test_run_sql_guardrails`、`test_structured_output` 等仍通过；它们均显式传参，不受默认值影响）。

---

## 6. 兼容性与注意事项

1. **行为变化**：未显式传 `read_only`/`max_rows` 的调用方现在默认只读 + 限行；需要写操作或全量返回时显式传参。
2. **既有测试/示例**：凡显式传参的调用不受影响；依赖默认可写的场景需自查。
3. **与 P1-06 护栏协同**：只读判定、行数截断、结果预算在 P1-06 已实现，本项只是把「安全档」设为默认。

---

## 7. 后续可扩展点

- 在 `AgentConfig` 层提供 `default_run_sql_read_only` / `default_max_rows`，让整个 Agent 统一配置；
- 对超限结果自动提示「已截断，可缩小查询或调整 max_rows」。

---

## 附：文件

- 修改：`src/vanna/tools/run_sql.py`
- 测试：`tests/test_public_api_defaults.py`
