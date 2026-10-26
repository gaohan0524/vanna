"""Wiring tests: build_agent must use the Stage 1 prompt builder."""

from __future__ import annotations

import asyncio

import pytest

from analytics_agent.agent_factory import build_agent, build_prompt_builder
from analytics_agent.config import Settings
from analytics_agent.context.glossary import DEFAULT_GLOSSARY
from analytics_agent.context.schema_catalog import DEFAULT_OUT
from analytics_agent.prompts.builder import AnalyticsPromptBuilder
from analytics_agent.prompts.templates import PROMPT_VERSION


def _settings(monkeypatch, catalog=DEFAULT_OUT, glossary=DEFAULT_GLOSSARY) -> Settings:
    monkeypatch.setenv("SCHEMA_CATALOG_PATH", str(catalog))
    monkeypatch.setenv("GLOSSARY_PATH", str(glossary))
    return Settings.from_env()


def test_build_prompt_builder_loads_assets():
    builder = build_prompt_builder(Settings.from_env())

    assert isinstance(builder, AnalyticsPromptBuilder)
    assert builder.prompt_version == PROMPT_VERSION
    assert "shop.orders" in builder.schema_text


def test_build_agent_uses_stage1_builder(monkeypatch):
    agent = build_agent(_settings(monkeypatch), llm_service=object())

    assert isinstance(agent.system_prompt_builder, AnalyticsPromptBuilder)
    assert agent.system_prompt_builder.prompt_version == PROMPT_VERSION


def test_catalog_tools_are_registered(monkeypatch):
    agent = build_agent(_settings(monkeypatch), llm_service=object())

    assert asyncio.run(agent.tool_registry.get_tool("search_columns")) is not None
    assert asyncio.run(agent.tool_registry.get_tool("describe_table")) is not None
    assert (
        asyncio.run(agent.tool_registry.get_tool("get_metric_definition")) is not None
    )

    names = set(asyncio.run(agent.tool_registry.list_tools()))
    assert {
        "run_sql",
        "visualize_data",
        "search_columns",
        "describe_table",
        "sample_rows",
        "explain_query",
        "get_metric_definition",
    } <= names


def test_missing_catalog_fails_fast(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, catalog=tmp_path / "missing.json")

    with pytest.raises(FileNotFoundError, match="refresh"):
        build_agent(settings, llm_service=object())


def test_missing_glossary_fails_fast(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, glossary=tmp_path / "missing.yaml")

    with pytest.raises(FileNotFoundError, match="glossary"):
        build_agent(settings, llm_service=object())
