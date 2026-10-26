"""Tests for sample_rows / explain_query and their safety rails."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics_agent.tools.explain_query import ExplainQueryArgs, ExplainQueryTool
from analytics_agent.tools.sample_rows import (
    SampleRowsArgs,
    SampleRowsTool,
    mask_frame,
    mask_value,
)


class _FakeRunner:
    def __init__(
        self, frame: pd.DataFrame | None = None, error: Exception | None = None
    ):
        self.frame = frame if frame is not None else pd.DataFrame()
        self.error = error
        self.calls = []

    async def run_query(self, sql: str, settings: dict | None = None):
        self.calls.append((sql, settings))
        if self.error:
            raise self.error
        return self.frame


def _catalog() -> dict:
    return {
        "database": "shop",
        "tables": [
            {"name": "orders", "rows": 1, "engine": "MergeTree", "columns": []},
            {"name": "users", "rows": 2, "engine": "MergeTree", "columns": []},
        ],
    }


def test_mask_value_masks_email_phone_and_truncates():
    assert mask_value("a@b.com") == "[email]"
    assert mask_value("电话 13800138000") == "电话 [phone]"
    long = "x" * 200
    masked = mask_value(long)
    assert len(masked) == 80
    assert masked.endswith("…")
    assert mask_value(123) == 123


def test_mask_frame_does_not_mutate_original():
    frame = pd.DataFrame({"email": ["a@b.com"], "n": [1]})
    masked = mask_frame(frame)
    assert masked.loc[0, "email"] == "[email]"
    assert frame.loc[0, "email"] == "a@b.com"


async def test_sample_rows_forces_limit_and_timeout():
    runner = _FakeRunner(pd.DataFrame({"email": ["a@b.com"]}))
    tool = SampleRowsTool(_catalog(), runner)

    result = await tool.execute(None, SampleRowsArgs(table="shop.orders", n=3))

    assert result.success
    sql, settings = runner.calls[0]
    assert "LIMIT 3" in sql
    assert settings == {"max_execution_time": 10.0}
    assert "[email]" in result.result_for_llm


async def test_sample_rows_unknown_table_is_rejected_without_query():
    runner = _FakeRunner()
    tool = SampleRowsTool(_catalog(), runner)

    result = await tool.execute(None, SampleRowsArgs(table="oder"))

    assert not result.success
    assert "orders" in result.result_for_llm
    assert runner.calls == []


async def test_sample_rows_wraps_database_error():
    runner = _FakeRunner(error=RuntimeError("boom"))
    tool = SampleRowsTool(_catalog(), runner)

    result = await tool.execute(None, SampleRowsArgs(table="orders"))

    assert not result.success
    assert result.error == "sample_failed"
    assert "boom" in result.result_for_llm


def test_sample_rows_n_range():
    with pytest.raises(ValueError):
        SampleRowsArgs(table="orders", n=0)
    with pytest.raises(ValueError):
        SampleRowsArgs(table="orders", n=21)


async def test_explain_only_accepts_select():
    runner = _FakeRunner(pd.DataFrame({"explain": ["read_rows: 10"]}))
    tool = ExplainQueryTool(runner)

    ok = await tool.execute(None, ExplainQueryArgs(sql="SELECT 1"))
    assert ok.success
    sql, settings = runner.calls[0]
    assert sql.startswith("EXPLAIN PLAN ")
    assert settings == {"max_execution_time": 10.0}

    for bad in (
        "INSERT INTO shop.orders VALUES (1)",
        "SHOW TABLES",
        "SELECT 1; SELECT 2",
    ):
        result = await tool.execute(None, ExplainQueryArgs(sql=bad))
        assert not result.success, bad
        assert (
            "error_type" not in result.result_for_llm or "拒绝" in result.result_for_llm
        )


async def test_clickhouse_runner_passes_query_settings(monkeypatch):
    import clickhouse_connect

    from vanna.integrations.clickhouse.sql_runner import ClickHouseRunner

    captured: dict = {}

    class _Result:
        column_names = ["x"]
        result_rows = [[1]]

    class _Client:
        def query(self, sql, settings=None):
            captured["settings"] = settings
            return _Result()

        def close(self):
            pass

    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kwargs: _Client())
    runner = ClickHouseRunner(
        host="h",
        database="shop",
        user="u",
        password="p",
        query_settings={"max_execution_time": 30},
    )

    frame = await runner.run_query("SELECT 1", settings={"max_execution_time": 10})

    assert captured["settings"] == {"max_execution_time": 10}
    assert list(frame.columns) == ["x"]


async def test_explain_truncates_long_output():
    frame = pd.DataFrame({"explain": ["line " + "x" * 100] * 100})
    tool = ExplainQueryTool(_FakeRunner(frame))

    result = await tool.execute(None, ExplainQueryArgs(sql="SELECT 1"))

    assert result.success
    assert "执行计划已截断" in result.result_for_llm
    assert result.metadata["truncated"] is True
