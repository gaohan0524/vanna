"""Structured SQL error classification and payloads."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.tools import RunSqlTool
from vanna.tools.sql_errors import classify_sql_error, error_payload, hint_for_error

from analytics_agent.tools.run_sql import AnalyticsRunSqlTool


def test_classify_execution_errors():
    assert classify_sql_error("Code: 47 Unknown identifier: foo").error_type == (
        "unknown_identifier"
    )
    assert (
        classify_sql_error("DB::Exception: Syntax error at position 10").error_type
        == "syntax_error"
    )
    assert classify_sql_error("Code: 159 TIMEOUT_EXCEEDED").error_type == "timeout"
    assert classify_sql_error("Memory limit exceeded").error_type == "memory_limit"
    assert classify_sql_error("Division by zero").error_type == "division_by_zero"
    assert classify_sql_error("boom").error_type == "sql_error"


def test_error_payload_is_json_with_hint():
    payload = error_payload("timeout", "query timed out", hint_for_error("timeout"))
    data = json.loads(payload)

    assert data["error_type"] == "timeout"
    assert data["message"] == "query timed out"
    assert data["hint"]


class _FakeFileSystem:
    async def write_file(self, filename, content, context, overwrite=True):
        return None


class _ErrorRunner:
    BLOCKING = False

    def __init__(self, message: str):
        self.message = message
        self.called = False

    async def run_sql(self, args, context):
        self.called = True
        raise RuntimeError(self.message)


async def test_guardrail_error_is_structured():
    runner = _ErrorRunner("should not run")
    tool = AnalyticsRunSqlTool(
        sql_runner=runner,
        file_system=_FakeFileSystem(),
        read_only=True,
    )

    result = await tool.execute(None, RunSqlToolArgs(sql="DROP TABLE shop.orders"))

    data = json.loads(result.result_for_llm)
    assert result.metadata["error_type"] == "read_only_violation"
    assert data["error_type"] == "read_only_violation"
    assert data["hint"]
    assert runner.called is False


async def test_execution_error_is_classified():
    runner = _ErrorRunner("Code: 47 Unknown identifier: foo")
    tool = AnalyticsRunSqlTool(
        sql_runner=runner,
        file_system=_FakeFileSystem(),
        read_only=True,
    )

    result = await tool.execute(None, RunSqlToolArgs(sql="SELECT foo FROM shop.orders"))

    data = json.loads(result.result_for_llm)
    assert result.metadata["error_type"] == "unknown_identifier"
    assert data["error_type"] == "unknown_identifier"
    assert "search_columns" in data["hint"]


async def test_framework_validation_error_is_structured():
    runner = _ErrorRunner("should not run")
    tool = RunSqlTool(
        sql_runner=runner,
        file_system=_FakeFileSystem(),
        read_only=True,
    )

    result = await tool.execute(None, RunSqlToolArgs(sql=""))

    data = json.loads(result.result_for_llm)
    assert result.metadata["error_type"] == "empty_sql"
    assert data["hint"]
    assert runner.called is False


async def test_sample_error_is_classified():
    from analytics_agent.tools.sample_rows import SampleRowsArgs, SampleRowsTool

    class _SampleRunner:
        async def run_query(self, sql, settings=None):
            raise RuntimeError("Code: 47 Unknown identifier: email")

    catalog = {
        "database": "shop",
        "tables": [{"name": "users", "rows": 1, "engine": "MergeTree", "columns": []}],
    }
    tool = SampleRowsTool(catalog, _SampleRunner())

    result = await tool.execute(None, SampleRowsArgs(table="users", n=1))

    data = json.loads(result.result_for_llm)
    assert result.error == "sample_failed"
    assert result.metadata["error_type"] == "unknown_identifier"
    assert data["hint"]
