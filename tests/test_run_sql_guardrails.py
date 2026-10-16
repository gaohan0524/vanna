"""
Tests for RunSqlTool SQL execution guardrails.

覆盖 run_sql 工具的企业级护栏：
- 只读模式（read_only）：拦截写操作
- 查询超时（timeout_seconds）：asyncio.wait_for 与 SQLite 原生 progress_handler
- 行数上限（max_rows）
- 结果文本预算（max_result_chars）
"""

import asyncio
import sqlite3

import pandas as pd
import pytest

from vanna.capabilities.sql_runner import RunSqlToolArgs, SqlRunner
from vanna.core.tool import ToolContext
from vanna.core.user import User
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.sqlite import SqliteRunner
from vanna.tools.run_sql import RunSqlTool, _is_read_only_query


@pytest.fixture
def context() -> ToolContext:
    """构造一个最小可用的工具执行上下文。"""
    user = User(id="test-user", email="test@example.com", group_memberships=["user"])
    return ToolContext(
        user=user,
        conversation_id="conv-1",
        request_id="req-1",
        agent_memory=DemoAgentMemory(max_items=100),
        metadata={},
    )


@pytest.fixture
def sqlite_db(tmp_path) -> SqliteRunner:
    """构造一个含 100 行 numbers 表的临时 SQLite 库。"""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE numbers (n INTEGER)")
    conn.executemany("INSERT INTO numbers VALUES (?)", [(i,) for i in range(100)])
    conn.commit()
    conn.close()
    return SqliteRunner(database_path=str(db))


class TestIsReadOnlyQuery:
    """测试只读 SQL 判断的边界行为。"""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM t",
            "select a from t",
            "  -- comment\n SELECT 1",
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "EXPLAIN SELECT 1",
            "SHOW TABLES",
            "/* c */ SELECT 1",
            "PRAGMA table_info(x)",
        ],
    )
    def test_read_only_queries_pass(self, sql):
        assert _is_read_only_query(sql) is True

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO t VALUES(1)",
            "UPDATE t SET a=1",
            "DELETE FROM t",
            "DROP TABLE t",
            "CREATE TABLE t (a int)",
            "WITH x AS (SELECT 1) DELETE FROM t",
            "SELECT * FROM t; DROP TABLE t;",
        ],
    )
    def test_write_queries_rejected(self, sql):
        assert _is_read_only_query(sql) is False

    def test_empty_query_rejected(self):
        assert _is_read_only_query("") is False
        assert _is_read_only_query("   ") is False


class TestReadOnlyGuardrail:
    """测试 read_only 模式在工具层的拦截。"""

    async def test_write_rejected_in_read_only_mode(self, sqlite_db, context):
        tool = RunSqlTool(sql_runner=sqlite_db, read_only=True)
        result = await tool.execute(
            context, RunSqlToolArgs(sql="INSERT INTO numbers VALUES (999)")
        )
        assert result.success is False
        assert result.metadata["error_type"] == "read_only_violation"

    async def test_select_allowed_in_read_only_mode(self, sqlite_db, context):
        tool = RunSqlTool(sql_runner=sqlite_db, read_only=True)
        result = await tool.execute(
            context, RunSqlToolArgs(sql="SELECT * FROM numbers")
        )
        assert result.success is True

    async def test_write_allowed_when_read_only_disabled(self, sqlite_db, context):
        tool = RunSqlTool(sql_runner=sqlite_db, read_only=False)
        result = await tool.execute(
            context, RunSqlToolArgs(sql="INSERT INTO numbers VALUES (999)")
        )
        assert result.success is True


class TestMaxRowsGuardrail:
    """测试行数上限截断。"""

    async def test_rows_truncated_to_max_rows(self, sqlite_db, context):
        tool = RunSqlTool(sql_runner=sqlite_db, max_rows=10)
        result = await tool.execute(
            context, RunSqlToolArgs(sql="SELECT * FROM numbers")
        )
        assert result.success is True
        assert result.metadata["row_count"] == 10
        assert result.metadata["total_rows"] == 100
        assert result.metadata["rows_truncated"] is True

    async def test_no_truncation_when_under_limit(self, sqlite_db, context):
        tool = RunSqlTool(sql_runner=sqlite_db, max_rows=1000)
        result = await tool.execute(
            context, RunSqlToolArgs(sql="SELECT * FROM numbers")
        )
        assert result.success is True
        assert result.metadata["row_count"] == 100
        assert result.metadata["rows_truncated"] is False


class TestMaxResultCharsGuardrail:
    """测试结果文本预算截断。"""

    async def test_result_text_truncated(self, sqlite_db, context):
        tool = RunSqlTool(sql_runner=sqlite_db, max_result_chars=50)
        result = await tool.execute(
            context, RunSqlToolArgs(sql="SELECT * FROM numbers")
        )
        assert result.success is True
        assert "(Results truncated to 50 characters" in result.result_for_llm


class _SlowRunner(SqlRunner):
    """真正异步的慢查询 runner，用于测试 asyncio.wait_for 超时。"""

    async def run_sql(self, args: RunSqlToolArgs, context: ToolContext) -> pd.DataFrame:
        await asyncio.sleep(1.0)
        return pd.DataFrame({"x": [1]})


class TestTimeoutGuardrail:
    """测试查询超时。"""

    async def test_timeout_via_wait_for(self, context):
        tool = RunSqlTool(sql_runner=_SlowRunner(), timeout_seconds=0.01)
        result = await tool.execute(context, RunSqlToolArgs(sql="SELECT 1"))
        assert result.success is False
        assert result.metadata["error_type"] == "query_timeout"

    async def test_sqlite_native_timeout(self, tmp_path, context):
        db = tmp_path / "slow.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
        conn.close()

        # tool 层不设 timeout，靠 SQLite runner 原生 progress_handler 超时
        runner = SqliteRunner(database_path=str(db), timeout_seconds=0.01)
        tool = RunSqlTool(sql_runner=runner)
        slow_query = (
            "WITH RECURSIVE cnt(x) AS ("
            "  SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x < 100000000"
            ") SELECT count(*) FROM cnt"
        )
        result = await tool.execute(context, RunSqlToolArgs(sql=slow_query))
        assert result.success is False
        assert result.metadata["error_type"] == "query_timeout"
