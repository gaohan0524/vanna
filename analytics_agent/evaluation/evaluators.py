"""Evaluators for the Stage 0 harness.

三个评测器分工：
- `ResultSetEvaluator`     answer 类：任意一条执行过的 SQL 命中 golden 结果集即通过
- `ClarificationEvaluator` clarify / refuse 类：该问才问、不该编造
- `FirstTryEvaluator`      answer 类：只看第一次 run_sql 是否命中（“一次写对率”）

注意：`AgentResult` 不包含工具执行结果，所以这里必须重放 SQL。
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional

import pandas as pd

from vanna.core.evaluation import (
    AgentResult,
    EvaluationResult,
    Evaluator,
    TestCase,
)

from .compare import results_equal
from .executor import execute_readonly, execute_safe

Executor = Callable[[str], Awaitable[pd.DataFrame]]

# 中文澄清常见的措辞（不强制带问号）
CLARIFY_CUES = (
    "请提供",
    "请说明",
    "请确认",
    "需要确认",
    "需要您",
    "哪一个",
    "哪个",
    "什么时间",
    "时间范围",
    "统计口径",
    "口径是",
)

# 明确拒答的措辞：仅仅反问用户“请提供数据”不算拒绝
REFUSE_CUES = (
    "无法",
    "没有",
    "暂无",
    "无数据",
    "不包含",
    "不提供",
    "查不到",
    "不存在",
    "不能",
    "未收录",
    "无相关",
    "无权",
)


def extract_sqls(agent_result: AgentResult) -> List[str]:
    """Return the SQL strings the agent asked ``run_sql`` to execute, in order."""
    sqls: List[str] = []
    for call in agent_result.tool_calls:
        if call.get("tool_name") != "run_sql":
            continue
        sql = (call.get("arguments") or {}).get("sql")
        if isinstance(sql, str) and sql.strip():
            sqls.append(sql)
    return sqls


def behavior_of(test_case: TestCase) -> str:
    """Expected behavior: ``answer`` (default) / ``clarify`` / ``refuse``."""
    return str((test_case.metadata or {}).get("expected_behavior", "answer"))


def golden_sql_of(test_case: TestCase) -> Optional[str]:
    sql = (test_case.metadata or {}).get("golden_sql")
    return sql if isinstance(sql, str) and sql.strip() else None


def _skip(name: str, test_case: TestCase, reason: str) -> EvaluationResult:
    return EvaluationResult(
        test_case_id=test_case.id,
        evaluator_name=name,
        passed=True,
        score=1.0,
        reasoning=f"skipped: {reason}",
        metrics={"skipped": True},
    )


def _fail(name: str, test_case: TestCase, reason: str, **metrics) -> EvaluationResult:
    return EvaluationResult(
        test_case_id=test_case.id,
        evaluator_name=name,
        passed=False,
        score=0.0,
        reasoning=reason,
        metrics=metrics,
    )


def _pass(name: str, test_case: TestCase, reason: str, **metrics) -> EvaluationResult:
    return EvaluationResult(
        test_case_id=test_case.id,
        evaluator_name=name,
        passed=True,
        score=1.0,
        reasoning=reason,
        metrics=metrics,
    )


class ResultSetEvaluator(Evaluator):
    """Pass when any executed SQL returns the golden result set."""

    def __init__(self, execute: Executor = execute_readonly) -> None:
        self._execute = execute

    @property
    def name(self) -> str:
        return "result_set"

    async def evaluate(
        self, test_case: TestCase, agent_result: AgentResult
    ) -> EvaluationResult:
        expected = behavior_of(test_case)
        if expected != "answer":
            return _skip(self.name, test_case, f"expected_behavior={expected}")

        golden_sql = golden_sql_of(test_case)
        if golden_sql is None:
            return _fail(self.name, test_case, "missing golden_sql")

        golden, golden_error = await execute_safe(golden_sql)
        if golden is None:
            return _fail(self.name, test_case, f"golden_sql failed: {golden_error}")

        sqls = extract_sqls(agent_result)
        if not sqls:
            return _fail(self.name, test_case, "agent did not call run_sql")

        order_sensitive = bool((test_case.metadata or {}).get("order_sensitive", False))
        column_alias = (test_case.metadata or {}).get("column_alias") or None

        last_reason = "no query produced a comparable result"
        failures: List[str] = []

        for sql in reversed(sqls):
            actual, error = await execute_safe(sql)
            if actual is None:
                failures.append(error or "unknown error")
                last_reason = f"query failed: {error}"
                continue

            comparison = results_equal(
                golden,
                actual,
                order_sensitive=order_sensitive,
                column_alias=column_alias,
                column_match=(test_case.metadata or {}).get("column_match", "values"),
            )
            if comparison.equal:
                return _pass(
                    self.name,
                    test_case,
                    f"matched: {sql[:160]}",
                    executed_sql=sql,
                    queries=len(sqls),
                )
            last_reason = f"{comparison.reason}: {comparison.detail}"

        return _fail(
            self.name,
            test_case,
            last_reason,
            queries=len(sqls),
            errors=failures[-3:],
        )


class ClarificationEvaluator(Evaluator):
    """Check clarify / refuse behaviors; pass-through for answer cases."""

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "clarification"

    async def evaluate(
        self, test_case: TestCase, agent_result: AgentResult
    ) -> EvaluationResult:
        expected = behavior_of(test_case)
        if expected == "answer":
            return _skip(self.name, test_case, "expected_behavior=answer")

        sqls = extract_sqls(agent_result)
        answer = agent_result.get_final_answer().strip()
        require_mark = bool(
            (test_case.metadata or {}).get("require_question_mark", True)
        )

        if expected == "clarify":
            if sqls:
                return _fail(
                    self.name,
                    test_case,
                    "asked to clarify but executed SQL anyway",
                    queries=len(sqls),
                )
            if not answer:
                return _fail(self.name, test_case, "empty answer")
            # 中文澄清不一定带问号（“请提供时间范围。”），因此同时接受线索词
            has_mark = "?" in answer or "？" in answer
            has_cue = any(cue in answer for cue in CLARIFY_CUES)
            if require_mark and not has_mark and not has_cue:
                return _fail(
                    self.name,
                    test_case,
                    "no clarifying question detected",
                    answer=answer[:200],
                )
            return _pass(self.name, test_case, "clarification requested")

        if expected == "refuse":
            # 允许用 run_sql 探查 schema/元数据来确认“数据不存在”；
            # 判定只看最终回答是否明确说明不可用，避免把“先查证再拒答”误判为失败。
            if not answer:
                return _fail(self.name, test_case, "empty answer")
            if not any(cue in answer for cue in REFUSE_CUES):
                return _fail(
                    self.name,
                    test_case,
                    "did not state the data is unavailable",
                    answer=answer[:200],
                )
            return _pass(
                self.name,
                test_case,
                "refused with data-unavailable wording",
                queries=len(sqls),
            )

        return _fail(self.name, test_case, f"unknown expected_behavior={expected!r}")


class FirstTryEvaluator(Evaluator):
    """Pass when the very first ``run_sql`` already matches golden."""

    def __init__(self, execute: Executor = execute_readonly) -> None:
        self._execute = execute

    @property
    def name(self) -> str:
        return "first_try"

    async def evaluate(
        self, test_case: TestCase, agent_result: AgentResult
    ) -> EvaluationResult:
        expected = behavior_of(test_case)
        if expected != "answer":
            return _skip(self.name, test_case, f"expected_behavior={expected}")

        golden_sql = golden_sql_of(test_case)
        if golden_sql is None:
            return _skip(self.name, test_case, "missing golden_sql")

        sqls = extract_sqls(agent_result)
        if not sqls:
            return _fail(self.name, test_case, "agent did not call run_sql")

        golden, golden_error = await execute_safe(golden_sql)
        if golden is None:
            return _fail(self.name, test_case, f"golden_sql failed: {golden_error}")

        actual, error = await execute_safe(sqls[0])
        if actual is None:
            return _fail(self.name, test_case, f"first query failed: {error}")

        comparison = results_equal(
            golden,
            actual,
            order_sensitive=bool(
                (test_case.metadata or {}).get("order_sensitive", False)
            ),
            column_alias=(test_case.metadata or {}).get("column_alias") or None,
            column_match=(test_case.metadata or {}).get("column_match", "values"),
        )
        if comparison.equal:
            return _pass(self.name, test_case, "first query matched")
        return _fail(
            self.name,
            test_case,
            f"{comparison.reason}: {comparison.detail}",
        )
