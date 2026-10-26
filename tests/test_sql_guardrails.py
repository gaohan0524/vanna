"""AST guardrails: read-only, multi-statement, FORMAT, LIMIT injection."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics_agent.tools.guardrails import SqlGuardError, prepare_sql
from analytics_agent.tools.run_sql import AnalyticsRunSqlTool
from vanna.capabilities.sql_runner import RunSqlToolArgs


def test_string_literal_delete_is_allowed():
    sql, note = prepare_sql("SELECT 'delete' AS s")

    assert sql.startswith("SELECT * FROM (")
    assert note.startswith("已自动追加 LIMIT")


def test_multiple_statements_are_rejected():
    with pytest.raises(SqlGuardError) as exc:
        prepare_sql("SELECT 1; DROP TABLE shop.orders")

    assert exc.value.error_type == "multiple_statements"


def test_with_insert_is_rejected_by_ast():
    with pytest.raises(SqlGuardError) as exc:
        prepare_sql(
            "WITH x AS (SELECT 1 AS n) INSERT INTO shop.orders (order_id) SELECT n FROM x"
        )

    assert exc.value.error_type == "read_only_violation"


def test_comment_drop_is_allowed():
    sql, _ = prepare_sql("/* DROP */ SELECT 1")

    assert sql.startswith("SELECT * FROM (")


def test_format_is_rejected():
    with pytest.raises(SqlGuardError) as exc:
        prepare_sql("SELECT * FROM shop.orders FORMAT JSON")

    assert exc.value.error_type == "unsupported_format"


def test_existing_limit_is_kept():
    sql, note = prepare_sql("SELECT * FROM shop.orders LIMIT 10")

    assert note == ""
    assert "LIMIT 10" in sql
    assert "SELECT * FROM (" not in sql


def test_parse_error_is_rejected():
    with pytest.raises(SqlGuardError) as exc:
        prepare_sql("SELECT FROM WHERE")

    assert exc.value.error_type == "parse_error"


class _FakeRunner:
    BLOCKING = False

    def __init__(self):
        self.sql = None

    async def run_sql(self, args, context):
        self.sql = args.sql
        return pd.DataFrame({"x": [1]})


class _FakeFileSystem:
    async def write_file(self, filename, content, context, overwrite=True):
        return None


async def test_run_sql_tool_injects_limit_and_reports_note():
    runner = _FakeRunner()
    tool = AnalyticsRunSqlTool(
        sql_runner=runner,
        file_system=_FakeFileSystem(),
        read_only=True,
        auto_limit=50,
    )

    result = await tool.execute(None, RunSqlToolArgs(sql="SELECT 1"))

    assert result.success
    assert "LIMIT 50" in runner.sql
    assert "已自动追加 LIMIT 50" in result.result_for_llm


async def test_string_literal_delete_survives_framework_validation():
    runner = _FakeRunner()
    tool = AnalyticsRunSqlTool(
        sql_runner=runner,
        file_system=_FakeFileSystem(),
        read_only=True,
    )

    result = await tool.execute(None, RunSqlToolArgs(sql="SELECT 'delete' AS s"))

    assert result.success
    assert "'delete'" in runner.sql


async def test_run_sql_tool_rejects_write_before_execution():
    runner = _FakeRunner()
    tool = AnalyticsRunSqlTool(
        sql_runner=runner,
        file_system=_FakeFileSystem(),
        read_only=True,
    )

    result = await tool.execute(None, RunSqlToolArgs(sql="DROP TABLE shop.orders"))

    assert not result.success
    assert result.metadata["error_type"] == "read_only_violation"
    assert runner.sql is None
