# P0-05 同步 DB 驱动不再阻塞事件循环

- **对应问题**：SQLite/Postgres/MySQL 等 runner 在 `async def` 里跑同步驱动，阻塞整条事件循环
- **优先级**：P0（可用性）
- **状态**：已完成
- **提交**：`b573980 perf: 同步 DB 驱动不再阻塞事件循环（P0-C）`

---

## 1. 问题背景

11 个 SQL runner 的 `run_sql` 都是 `async def`，但内部调用同步数据库驱动：

```python
# sqlite/sql_runner.py
async def run_sql(self, args, context):
    ...
    self._conn.execute(...)   # sqlite3 同步阻塞
```

`async def` 并不会让同步调用变成非阻塞。慢查询会占住唯一的事件循环线程，导致同一进程上的**所有** SSE/WebSocket/健康检查同时停摆——这正是 P0-01（LLM 同步 SDK）在数据库层的同类问题。

`RunSqlTool` 用 `asyncio.wait_for` 包住它，但 `wait_for` 也无法中断一个正在阻塞的同步调用，超时形同虚设。

---

## 2. 主流方案参考

| 方案 | 做法 |
|---|---|
| 异步驱动 | asyncpg / aiomysql / aiosqlite 等原生异步客户端 |
| 同步驱动 + 线程池 | `asyncio.to_thread` / `loop.run_in_executor` 把阻塞调用丢到 worker 线程 |
| 数据库原生超时 | `statement_timeout`（Postgres）/ `progress_handler`（SQLite）/ 连接超时 |

本项目已有 SQLite/Postgres 原生超时（P1-06），本次补上「同步驱动不阻塞主循环」，采用**线程池 offload**，零新增依赖、覆盖全部 11 个 runner。

---

## 3. 方案设计

### 3.1 `SqlRunner.BLOCKING` 标记

给抽象基类加一个类属性，由各实现声明是否为同步驱动：

```python
class SqlRunner(ABC):
    BLOCKING: bool = False   # 同步驱动实现置 True；异步驱动保持 False
```

11 个同步 runner（sqlite/postgres/mysql/snowflake/presto/oracle/mssql/hive/duckdb/clickhouse/bigquery）均标 `BLOCKING = True`。**自定义异步 runner 保持 `False`，不受影响。**

### 3.2 `RunSqlTool` 按标记 offload

```python
def _run_sql(self, args, context):
    runner = self.sql_runner
    if getattr(runner, "BLOCKING", False):
        return asyncio.to_thread(_run_blocking_in_loop, runner, args, context)
    return runner.run_sql(args, context)
```

`_run_blocking_in_loop` 在 worker 线程里用独立事件循环跑完这个 `async def` 协程：

```python
def _run_blocking_in_loop(runner, args, context):
    return asyncio.run(runner.run_sql(args, context))
```

选择「标记 + 线程池」而非「改每个 runner 为 `to_thread`」的原因：`run_sql` 是 `async def`（接口约定），直接 `to_thread(self.sql_runner.run_sql, ...)` 会拿到一个协程而不会执行；而用 `asyncio.run` 在独立线程跑，既兼容现有接口，又对将来新增的同步驱动零改动生效。

### 3.3 超时语义

`asyncio.wait_for` 现在能真正**取消等待**（把主循环让给其它请求）；但 worker 线程里的同步调用无法被强行终止，因此超时仍以**数据库原生超时**（`statement_timeout` / `progress_handler` / 连接超时）为准——两者配合。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `capabilities/sql_runner/base.py` | 新增 `BLOCKING: bool = False` |
| 11 个 `integrations/*/sql_runner.py` | 各 runner 类加 `BLOCKING = True` |
| `tools/run_sql.py` | 新增 `_run_sql` / `_run_blocking_in_loop`，`execute` 改走 `_run_sql` |
| `tests/test_sql_runner_nonblocking.py` | 新增 4 个用例 |

### 关键代码

```python
def _run_blocking_in_loop(runner, args, context):
    return asyncio.run(runner.run_sql(args, context))
```

---

## 5. 验证

`tests/test_sql_runner_nonblocking.py`（4 passed）：

- `BLOCKING=True` 的自定义 runner：`run_sql` 里 `asyncio.get_running_loop()` 是**独立线程的新 loop**（≠ 主循环）；
- `BLOCKING=False` 的自定义 runner：仍在主循环执行；
- 内置 `SqliteRunner.BLOCKING is True`；
- `BLOCKING=True` 的慢 runner（`time.sleep(0.2)`）执行时，事件循环 ticker 仍持续推进（未被阻塞）。

---

## 6. 兼容性与注意事项

1. **异步 runner 不受影响**：`BLOCKING=False` 走原路径。
2. **线程池开销**：每个同步查询会新建一个独立事件循环（`asyncio.run`）；对典型查询可忽略，高频查询可后续改为持久线程 + 循环复用。
3. **超时**：`wait_for` 取消等待，但 worker 线程的同步调用无法被杀；务必配置数据库原生超时。
4. **工具上下文**：内置 runner 只读取 `context.user`，不触碰 `agent_memory` 等主循环绑定的异步资源，因此跨线程安全。

---

## 7. 后续可扩展点

- 替换为原生异步驱动（asyncpg/aiomysql/aiosqlite）并置 `BLOCKING=False`；
- 用持久 worker 线程 + 单例事件循环降低 `asyncio.run` 开销；
- 为 `RunSqlTool` 增加全局查询并发上限（信号量）。

---

## 附：文件

- 修改：`capabilities/sql_runner/base.py`、11 个 `sql_runner.py`、`tools/run_sql.py`
- 测试：`tests/test_sql_runner_nonblocking.py`
