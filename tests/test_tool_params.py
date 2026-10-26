"""Parameter convergence tests: ranges, formats, extra fields, whitespace."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from vanna import User

from analytics_agent.agent_factory import build_agent
from analytics_agent.config import Settings
from analytics_agent.context.glossary import DEFAULT_GLOSSARY
from analytics_agent.context.schema_catalog import DEFAULT_OUT
from analytics_agent.tools.describe_table import DescribeTableArgs
from analytics_agent.tools.explain_query import ExplainQueryArgs
from analytics_agent.tools.metric_definition import GetMetricDefinitionArgs
from analytics_agent.tools.sample_rows import SampleRowsArgs
from analytics_agent.tools.search_columns import SearchColumnsArgs


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs"):
        SearchColumnsArgs(keyword="gmv", limit=5, unexpected="x")

    with pytest.raises(ValidationError, match="Extra inputs"):
        SampleRowsArgs(table="orders", n=3, sql="SELECT 1")


def test_whitespace_is_stripped_before_length_check():
    args = GetMetricDefinitionArgs(name="  GMV  ")
    assert args.name == "GMV"

    with pytest.raises(ValidationError):
        SearchColumnsArgs(keyword="   ")


def test_numeric_ranges_report_limits():
    with pytest.raises(ValidationError, match="less than or equal to 20"):
        SampleRowsArgs(table="orders", n=21)

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        SampleRowsArgs(table="orders", n=0)

    with pytest.raises(ValidationError, match="less than or equal to 50"):
        SearchColumnsArgs(keyword="gmv", limit=51)


def test_table_name_pattern_rejects_injection():
    for bad in ("orders; DROP TABLE shop.orders", "shop.orders`", "orders where 1=1"):
        with pytest.raises(ValidationError):
            DescribeTableArgs(table=bad)
        with pytest.raises(ValidationError):
            SampleRowsArgs(table=bad)


def test_explain_sql_length_bounds():
    with pytest.raises(ValidationError):
        ExplainQueryArgs(sql="SELEC")

    with pytest.raises(ValidationError):
        ExplainQueryArgs(sql="SELECT " + "x" * 4_000)


def test_table_name_allows_database_prefix():
    assert DescribeTableArgs(table="shop.orders").table == "shop.orders"


def test_tool_schemas_build_with_forbid_config(monkeypatch):
    monkeypatch.setenv("SCHEMA_CATALOG_PATH", str(DEFAULT_OUT))
    monkeypatch.setenv("GLOSSARY_PATH", str(DEFAULT_GLOSSARY))
    agent = build_agent(Settings.from_env(), llm_service=object())
    user = User(id="u", group_memberships=["analyst"])

    schemas = asyncio.run(agent.tool_registry.get_schemas(user))
    names = {schema.name for schema in schemas}

    assert {
        "run_sql",
        "search_columns",
        "describe_table",
        "sample_rows",
        "explain_query",
        "get_metric_definition",
    } <= names
    for schema in schemas:
        assert schema.parameters.get("type") == "object"
