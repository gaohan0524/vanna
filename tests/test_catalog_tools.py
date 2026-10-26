"""Tests for catalog search helpers and the two catalog-backed tools."""

from __future__ import annotations

import pytest

from analytics_agent.context.search import (
    find_table,
    search_columns,
    suggest_tables,
)
from analytics_agent.tools.describe_table import DescribeTableArgs, DescribeTableTool
from analytics_agent.tools.search_columns import SearchColumnsArgs, SearchColumnsTool


def _catalog() -> dict:
    return {
        "version": 1,
        "database": "shop",
        "tables": [
            {
                "name": "orders",
                "engine": "MergeTree",
                "rows": 36000,
                "summary": "订单主表，一单一行；金额单位为分",
                "columns": [
                    {
                        "name": "order_id",
                        "type": "UInt64",
                        "comment": "订单唯一 ID",
                        "enum": None,
                    },
                    {
                        "name": "amount_cents",
                        "type": "Int64",
                        "comment": "订单金额（分）",
                        "enum": None,
                    },
                    {
                        "name": "channel",
                        "type": "LowCardinality(String)",
                        "comment": "首触渠道；取值: organic/paid_search",
                        "enum": ["organic", "paid_search"],
                    },
                ],
            },
            {
                "name": "users",
                "engine": "MergeTree",
                "rows": 5000,
                "summary": "用户表",
                "columns": [
                    {
                        "name": "email",
                        "type": "String",
                        "comment": "注册邮箱",
                        "enum": None,
                    }
                ],
            },
        ],
    }


def test_search_columns_expands_business_synonym():
    hits = search_columns(_catalog(), "gmv")

    assert hits[0]["table"]["name"] == "orders"
    assert hits[0]["column"]["name"] == "amount_cents"
    assert hits[0]["score"] > 0


def test_search_columns_matches_comment_and_limit():
    hits = search_columns(_catalog(), "渠道", limit=1)

    assert len(hits) == 1
    assert hits[0]["column"]["name"] == "channel"


def test_search_columns_empty_keyword():
    assert search_columns(_catalog(), "  ") == []


def test_find_table_accepts_database_prefix():
    assert find_table(_catalog(), "shop.orders")["name"] == "orders"
    assert find_table(_catalog(), "missing") is None


def test_suggest_tables_for_typo():
    assert "orders" in suggest_tables(_catalog(), "oder")


async def test_search_tool_returns_columns():
    tool = SearchColumnsTool(_catalog())

    result = await tool.execute(None, SearchColumnsArgs(keyword="gmv"))

    assert result.success
    assert "orders.amount_cents" in result.result_for_llm
    assert "订单金额" in result.result_for_llm


async def test_search_tool_no_hits_gives_hint():
    tool = SearchColumnsTool(_catalog())

    result = await tool.execute(None, SearchColumnsArgs(keyword="完全不存在的词"))

    assert result.success
    assert "未找到" in result.result_for_llm
    assert "describe_table" in result.result_for_llm


async def test_describe_tool_formats_table():
    tool = DescribeTableTool(_catalog())

    result = await tool.execute(None, DescribeTableArgs(table="shop.orders"))

    assert result.success
    assert "36,000 行" in result.result_for_llm
    assert "字段（3）" in result.result_for_llm
    assert "取值: organic/paid_search" in result.result_for_llm


async def test_describe_tool_unknown_table_suggests():
    tool = DescribeTableTool(_catalog())

    result = await tool.execute(None, DescribeTableArgs(table="oder"))

    assert not result.success
    assert "orders" in result.result_for_llm


def test_describe_args_reject_empty_table():
    with pytest.raises(ValueError):
        DescribeTableArgs(table="")
