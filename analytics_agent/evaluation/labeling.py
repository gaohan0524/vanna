"""Failure taxonomy labeling.

分类不是目的，目的是让每周的“修哪一类”有依据。规则按
`docs/STAGE0-EVAL.md` 7.6 的优先级从上到下匹配，命中即停。
"""

from __future__ import annotations

from typing import List

from vanna.core.evaluation import AgentResult, TestCase

from .compare import results_equal
from .evaluators import behavior_of, extract_sqls, golden_sql_of
from .executor import execute_safe

TAXONOMY = (
    "OK",
    "AMBIGUOUS",
    "NO_TOOL",
    "SCHEMA_MISSING",
    "DIALECT_ERROR",
    "TIMEOUT",
    "SHAPE_MISMATCH",
    "FILTER_MISSING",
    "METRIC_WRONG",
    "UNKNOWN",
)

_SCHEMA_HINTS = (
    "unknown identifier",
    "missing columns",
    "unknown table",
    "no such column",
    "doesn't exist",
    "does not exist",
)
_DIALECT_HINTS = (
    "syntax error",
    "cannot parse",
    "unsupported",
    "expected",
    "illegal",
)
_TIMEOUT_HINTS = ("timeout", "timed out", "max_execution_time", "code: 159")


def _match(error: str, hints: tuple[str, ...]) -> bool:
    lowered = error.lower()
    return any(hint in lowered for hint in hints)


async def classify(test_case: TestCase, agent_result: AgentResult) -> str:
    """Return one taxonomy code for a finished test case."""
    expected = behavior_of(test_case)
    sqls = extract_sqls(agent_result)
    answer = agent_result.get_final_answer().strip()

    # 1) 期望澄清/拒答，但行为不符
    if expected == "clarify":
        has_question = "?" in answer or "？" in answer
        return "OK" if not sqls and has_question else "AMBIGUOUS"
    if expected == "refuse":
        return "OK" if not sqls and answer else "AMBIGUOUS"

    # 2) 该答却没调用工具
    if not sqls:
        return "NO_TOOL"

    golden_sql = golden_sql_of(test_case)
    if golden_sql is None:
        return "OK"

    golden, golden_error = await execute_safe(golden_sql)
    if golden is None:
        return "UNKNOWN"

    errors: List[str] = []
    order_sensitive = bool((test_case.metadata or {}).get("order_sensitive", False))
    column_alias = (test_case.metadata or {}).get("column_alias") or None

    executed = 0
    comparisons = []
    for sql in sqls:
        actual, error = await execute_safe(sql)
        if actual is None:
            errors.append(error or "unknown error")
            continue
        executed += 1
        comparisons.append(
            results_equal(
                golden,
                actual,
                order_sensitive=order_sensitive,
                column_alias=column_alias,
            )
        )

    if any(comparison.equal for comparison in comparisons):
        return "OK"

    # 3) 全部失败：按错误信息归因
    if executed == 0:
        joined = " | ".join(errors)
        if _match(joined, _SCHEMA_HINTS):
            return "SCHEMA_MISSING"
        if _match(joined, _TIMEOUT_HINTS):
            return "TIMEOUT"
        if _match(joined, _DIALECT_HINTS):
            return "DIALECT_ERROR"
        return "UNKNOWN"

    # 4) 执行成功但结果不符
    if any(comparison.reason == "shape" for comparison in comparisons):
        return "SHAPE_MISMATCH"

    without_filter_sql = (test_case.metadata or {}).get("without_filter_sql")
    if isinstance(without_filter_sql, str) and without_filter_sql.strip():
        variant, variant_error = await execute_safe(without_filter_sql)
        if variant is not None:
            for sql in sqls:
                actual, _ = await execute_safe(sql)
                if actual is None:
                    continue
                if results_equal(variant, actual, column_alias=column_alias).equal:
                    return "FILTER_MISSING"

    return "METRIC_WRONG"
