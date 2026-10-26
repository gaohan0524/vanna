"""Every registered tool must use the four-part description format."""

from __future__ import annotations

import asyncio

from analytics_agent.agent_factory import build_agent
from analytics_agent.config import Settings
from analytics_agent.context.glossary import DEFAULT_GLOSSARY
from analytics_agent.context.schema_catalog import DEFAULT_OUT

TOOL_NAMES = (
    "run_sql",
    "visualize_data",
    "search_columns",
    "describe_table",
    "sample_rows",
    "explain_query",
    "get_metric_definition",
)


def _settings(monkeypatch) -> Settings:
    monkeypatch.setenv("SCHEMA_CATALOG_PATH", str(DEFAULT_OUT))
    monkeypatch.setenv("GLOSSARY_PATH", str(DEFAULT_GLOSSARY))
    return Settings.from_env()


def test_all_tools_use_four_part_descriptions(monkeypatch):
    agent = build_agent(_settings(monkeypatch), llm_service=object())

    for name in TOOL_NAMES:
        tool = asyncio.run(agent.tool_registry.get_tool(name))
        assert tool is not None, name
        description = tool.description
        for marker in ("用：", "不要用：", "分工："):
            assert marker in description, (name, marker, description)


def test_run_sql_description_warns_about_division_of_labor(monkeypatch):
    agent = build_agent(_settings(monkeypatch), llm_service=object())

    tool = asyncio.run(agent.tool_registry.get_tool("run_sql"))
    assert tool.description.startswith("run_sql：")
    assert "search_columns" in tool.description
    assert "失败只改一次" in tool.description
