# P1-06 SQL 执行护栏

- **对应问题**：#6 SQL 执行缺少护栏
- **提交**：`56628d3 feat: SQL 执行护栏（只读/超时/行数上限/结果预算）与 sqlite/postgres 原生超时`
- **优先级**：P1（功能/体验差距）
- **状态**：已完成

---

## 1. 问题背景

优化前，`RunSqlTool` 收到 LLM 生成的 SQL 后直接交给 `SqlRunner` 执行：

- **无只读限制**：`DELETE` / `DROP` 等写操作可直接执行；
- **无查询超时**：慢查询会一直占住连接/线程；
- **无行数上限**：大结果集全部读入内存、写 CSV、回喂 LLM；
- **结果截断硬编码**：1000 字符不可配置；
- **无统一错误分类**：失败只能看原始异常文本。

企业级 Text2SQL 通常需要：`read_only` / `query timeout` / `max_rows` / 结果预算（以及后续的 SQL 结构校验）。

---

## 2. 主流方案参考

| 能力 | 主流做法 |
|---|---|
| 只读保护 | 只允许 `SELECT/WITH/EXPLAIN/SHOW/...`，拒绝 DML/DDL |
| 查询超时 | 数据库原生 `statement_timeout` / `progress_handler`，或客户端 `asyncio.wait_for` |
| 行数上限 | `LIMIT` 改写或结果集 `head(max_rows)` |
| 结果预算 | 回喂 LLM 的文本按字符/token 截断 |
| 错误分类 | 结构化 `error_type`，回喂 LLM 自我修复 |

---

## 3. 方案设计

### 3.1 构造参数即护栏（不可被 LLM 绕过）

护栏通过 `RunSqlTool` 的构造参数配置，而不是暴露给 LLM 的参数：

```python
RunSqlTool(
    sql_runner=...,
    read_only=True,            # 拒绝写操作
    timeout_seconds=30,        # 查询超时
    max_rows=1000,             # 返回行数上限
    max_result_chars=1000,     # 回喂 LLM 的结果文本预算
)
```

这样护栏由服务端强制生效，模型无法通过 tool args 关闭。

### 3.2 只读判定

先剥离注释，再判断首个关键字是否属于只读白名单，并全文扫描写关键字（防御 `WITH ... DELETE`、堆叠语句等）：

```python
_READ_ONLY_LEADING_KEYWORDS = {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC", "PRAGMA"}
_WRITE_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", ...}

def _is_read_only_query(sql: str) -> bool:
    cleaned = _strip_sql_comments(sql).strip()
    leading = cleaned.split(None, 1)[0].upper()
    if leading not in _READ_ONLY_LEADING_KEYWORDS:
        return False
    return not any(re.search(rf"\b{kw}\b", cleaned.upper()) for kw in _WRITE_KEYWORDS)
```

### 3.3 超时

- **工具层**：`asyncio.wait_for(self.sql_runner.run_sql(...), timeout=...)`，适用于真正异步的 runner。
- **驱动层**：
  - SQLite：`set_progress_handler` 每 N 条 VM 指令检查截止时间，超时抛 `TimeoutError`；
  - PostgreSQL：`SET statement_timeout = ...`，捕获 `QueryCanceled` 后转 `TimeoutError`。

> 同步驱动（psycopg2 / sqlite3）阻塞在 C 调用时，`asyncio.wait_for` 无法中断线程，因此必须配合数据库原生超时。

### 3.4 行数上限与结果预算

```python
total_rows = len(df)
rows_truncated = False
if self.max_rows is not None and len(df) > self.max_rows:
    df = df.head(self.max_rows)
    rows_truncated = True

results_preview = csv_content
if len(results_preview) > self.max_result_chars:
    results_preview = results_preview[: self.max_result_chars] + "(Results truncated ...)"
```

`metadata` 中记录 `row_count` / `total_rows` / `rows_truncated`，便于前端与审计。

### 3.5 结构化错误

失败时返回 `ToolResult(success=False, metadata={"error_type": ...})`：

| `error_type` | 场景 |
|---|---|
| `read_only_violation` | 只读模式下出现写操作 |
| `query_timeout` | 查询超时 |
| `sql_error` | 数据库执行报错（语法/表不存在等） |

错误文本同时作为 `result_for_llm` 回喂 LLM，模型可在下一轮自我修复。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `src/vanna/tools/run_sql.py` | 新增 `read_only` / `timeout_seconds` / `max_rows` / `max_result_chars`；只读判定；超时/截断/错误分类 |
| `src/vanna/integrations/sqlite/sql_runner.py` | 新增 `timeout_seconds`，用 `progress_handler` 实现原生超时 |
| `src/vanna/integrations/postgres/sql_runner.py` | 新增 `timeout_seconds`，用 `statement_timeout` 实现原生超时 |
| `tests/test_run_sql_guardrails.py` | 只读/超时/行数/结果预算测试 |

### 关键代码

```python
# 只读拦截
if self.read_only and not _is_read_only_query(args.sql):
    return ToolResult(
        success=False,
        result_for_llm="Write operations are not allowed in read-only mode.",
        metadata={"error_type": "read_only_violation"},
    )

# 超时
try:
    df = await asyncio.wait_for(
        self.sql_runner.run_sql(args, context), timeout=self.timeout_seconds
    )
except (TimeoutError, asyncio.TimeoutError) as e:
    return ToolResult(..., metadata={"error_type": "query_timeout"})
```

---

## 5. 使用方式

```python
from vanna.tools import RunSqlTool
from vanna.integrations.postgres import PostgresRunner

db_tool = RunSqlTool(
    sql_runner=PostgresRunner(connection_string="postgresql://..."),
    read_only=True,
    timeout_seconds=30,
    max_rows=1000,
    max_result_chars=2000,
)
```

> 后续 `c0a269a` 在护栏基础上又增加了 `max_sql_length` / `require_single_statement`，并把 SQL 校验函数抽到 `vanna/tools/sql_validation.py`（详见 P1-10）。

---

## 6. 验证

`tests/test_run_sql_guardrails.py`：

- `_is_read_only_query` 边界：只读语句通过、写语句/堆叠语句拒绝、空语句拒绝；
- `read_only=True` 时写操作被拦截且 `error_type == "read_only_violation"`；
- `max_rows` 截断且 `rows_truncated == True`；
- `max_result_chars` 截断提示；
- `timeout_seconds` 通过 `asyncio.wait_for` 与 SQLite 原生 `progress_handler` 两种路径触发超时。

---

## 7. 兼容性与注意事项

1. **默认值**：`read_only=False`、`timeout_seconds=None`、`max_rows=None`，即默认行为与优化前一致（不强制改变）。
2. **只读判定是启发式**：基于关键字与注释剥离，复杂 SQL 建议结合 AST 校验（可接入 sqlglot）。
3. **同步驱动器超时**：务必配置驱动原生超时，`asyncio.wait_for` 对阻塞线程无效。
4. **结果预算**：截断只影响回喂 LLM 的文本，UI 表格仍按 `max_rows` 展示。
5. **写操作**：生产环境建议默认 `read_only=True`，确需写入时显式关闭并配合审批（见 #9 human-in-the-loop）。

---

## 8. 后续可扩展点

- 接入 `sqlglot` 做方言识别、AST 级只读校验、表/列存在性检查；
- 支持 `LIMIT` 自动改写（而非结果集 `head`）；
- 增加查询取消（结合 P1-07 的取消信号）；
- 增加危险操作审批闸门（#9）。

---

## 附：提交与文件

- 提交：`56628d3`
- 文件：`src/vanna/tools/run_sql.py`、`src/vanna/integrations/sqlite/sql_runner.py`、`src/vanna/integrations/postgres/sql_runner.py`
- 测试：`tests/test_run_sql_guardrails.py`
