"""Tests for metric lookup and get_metric_definition."""

from __future__ import annotations

import pytest

from analytics_agent.context.glossary import (
    find_metric,
    list_metric_names,
    load_glossary,
    suggest_metrics,
)
from analytics_agent.tools.metric_definition import (
    GetMetricDefinitionArgs,
    GetMetricDefinitionTool,
)


def test_find_metric_by_key_name_case_and_alias():
    glossary = load_glossary()

    assert find_metric(glossary, "gmv")[0] == "gmv"
    assert find_metric(glossary, "GMV")[0] == "gmv"
    assert find_metric(glossary, "净收入")[0] == "net_revenue"
    assert find_metric(glossary, "成交额")[0] == "gmv"
    assert find_metric(glossary, "各渠道 GMV 是多少")[0] == "gmv"
    assert find_metric(glossary, "复购率")[0] == "repurchase_rate"


def test_find_metric_unknown_returns_none():
    glossary = load_glossary()

    assert find_metric(glossary, "毛利率") is None
    assert find_metric(glossary, "  ") is None


def test_suggest_and_list_metrics():
    glossary = load_glossary()

    assert "GMV" in list_metric_names(glossary)
    suggestions = suggest_metrics(glossary, "gpm")
    assert suggestions
    assert set(suggestions) <= set(list_metric_names(glossary))


async def test_metric_tool_returns_definition_formula_pitfalls():
    tool = GetMetricDefinitionTool(load_glossary())

    result = await tool.execute(None, GetMetricDefinitionArgs(name="净收入"))

    assert result.success
    assert "指标：净收入" in result.result_for_llm
    assert "公式：" in result.result_for_llm
    assert "易错点：" in result.result_for_llm


async def test_metric_tool_unknown_lists_candidates():
    tool = GetMetricDefinitionTool(load_glossary())

    result = await tool.execute(None, GetMetricDefinitionArgs(name="毛利率"))

    assert result.success
    assert "未找到指标" in result.result_for_llm
    assert "GMV" in result.result_for_llm
    assert "不要自行编造" in result.result_for_llm


def test_metric_args_reject_empty_name():
    with pytest.raises(ValueError):
        GetMetricDefinitionArgs(name="")
