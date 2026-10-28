"""Unit tests for prompt assembly (no network, no database)."""

from __future__ import annotations

from datetime import date

from vanna.core.user import User

from analytics_agent.context.render import estimate_tokens
from analytics_agent.prompts.builder import AnalyticsPromptBuilder
from analytics_agent.prompts.templates import L0_RULES, PROMPT_VERSION

SCHEMA = "### shop.orders — 订单主表\n- status LowCardinality(String) — 订单状态"
GLOSSARY = "## 指标定义\n- GMV：SUM(amount_cents)/100"


async def _build(glossary: str = GLOSSARY) -> str:
    builder = AnalyticsPromptBuilder(
        schema_text=SCHEMA,
        glossary_text=glossary,
        today=lambda: date(2026, 10, 25),
    )
    return await builder.build_system_prompt(User(id="u"), [])


async def test_prompt_contains_date_and_core_rules():
    prompt = await _build()

    assert "今天日期：2026-10-25" in prompt
    assert "ClickHouse" in prompt
    assert "有效订单" in prompt
    assert "当前账号无权限" in prompt
    assert "不查 system.*" in prompt


async def test_prompt_order_is_rules_then_schema_then_glossary():
    prompt = await _build()

    assert prompt.index("## 行为规则") < prompt.index("shop.orders")
    assert prompt.index("shop.orders") < prompt.index("## 指标定义")


async def test_glossary_is_optional():
    prompt = await _build(glossary="")

    assert "## 指标定义" not in prompt


async def test_no_tool_block_when_tools_empty():
    prompt = await _build()

    assert "当前账号无可用工具" in prompt
    assert "当前账号无权限查询" in prompt


async def test_prompt_declares_monday_week_start():
    prompt = await _build()

    assert "周一" in prompt
    assert "toStartOfWeek(x, 1)" in prompt
    assert "不用默认周日" in prompt
    assert "近 N 个月" in prompt
    assert "不含本月" in prompt
    assert "多轮追问" in prompt
    assert "单行单列" in prompt
    assert "不 GROUP BY" in prompt


async def test_prompt_contains_tool_routing():
    prompt = await _build()

    assert "## 工具使用" in prompt
    assert "search_columns" in prompt
    assert "sample_rows" in prompt
    assert "explain_query" in prompt
    assert "get_metric_definition" in prompt
    assert "run_sql" in prompt


async def test_prompt_budget():
    prompt = await _build()

    assert estimate_tokens(prompt) < 4_500


async def test_l0_budget():
    assert estimate_tokens(L0_RULES) <= 1_100


def test_prompt_version_is_set():
    assert PROMPT_VERSION.startswith("stage2-")
