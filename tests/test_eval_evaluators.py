"""Unit tests for ClarificationEvaluator (no database required)."""

from __future__ import annotations

from vanna.core.evaluation import AgentResult
from vanna.core.evaluation import TestCase as EvalCase
from vanna.core.user import User

from analytics_agent.evaluation.evaluators import ClarificationEvaluator
from analytics_agent.evaluation.labeling import classify

USER = User(id="analyst", email="analyst@example.com", group_memberships=["analyst"])


def _case(behavior: str) -> EvalCase:
    return EvalCase(
        id="E-01",
        user=USER,
        message="看一下复购率",
        metadata={"expected_behavior": behavior},
    )


def _result(answer: str, sql: str | None = None) -> AgentResult:
    result = AgentResult(test_case_id="E-01", components=[])
    result.get_final_answer = lambda: answer  # type: ignore[method-assign]
    if sql:
        result.tool_calls = [{"tool_name": "run_sql", "arguments": {"sql": sql}}]
    return result


async def test_clarify_accepts_question_mark():
    outcome = await ClarificationEvaluator().evaluate(
        _case("clarify"), _result("统计哪个时间范围？")
    )
    assert outcome.passed


async def test_clarify_accepts_chinese_cue_without_question_mark():
    outcome = await ClarificationEvaluator().evaluate(
        _case("clarify"), _result("请提供时间范围与统计口径。")
    )
    assert outcome.passed


async def test_clarify_rejects_plain_answer():
    outcome = await ClarificationEvaluator().evaluate(
        _case("clarify"), _result("复购率是 12.3%。")
    )
    assert not outcome.passed


async def test_clarify_rejects_sql_execution():
    outcome = await ClarificationEvaluator().evaluate(
        _case("clarify"),
        _result("请提供时间范围。", sql="SELECT 1"),
    )
    assert not outcome.passed


async def test_refuse_requires_no_sql():
    passed = await ClarificationEvaluator().evaluate(
        _case("refuse"), _result("库内没有竞品数据，无法查询。")
    )
    assert passed.passed

    failed = await ClarificationEvaluator().evaluate(
        _case("refuse"), _result("无法查询。", sql="SELECT 1")
    )
    assert not failed.passed


async def test_refuse_rejects_asking_user_for_data():
    """反问用户“请提供竞品表”不等于拒答，不能算通过。"""
    outcome = await ClarificationEvaluator().evaluate(
        _case("refuse"), _result("请提供你想要查询的竞品名称和数据表。")
    )
    assert not outcome.passed


async def test_labeling_matches_evaluator_for_refuse():
    assert (
        await classify(_case("refuse"), _result("库内没有竞品数据，无法查询。")) == "OK"
    )
    assert (
        await classify(_case("refuse"), _result("请提供你想要查询的竞品名称。"))
        == "AMBIGUOUS"
    )
