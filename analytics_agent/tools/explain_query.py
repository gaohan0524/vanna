"""`explain_query` 工具：在真正执行前查看 ClickHouse 的执行计划。

定位：
- 需要连数据库，但**不返回业务数据**；
- 用于大表/多表 join 的前置检查：扫描行数、分区裁剪、是否全表扫；
- 只接受单条 SELECT/WITH，避免把 EXPLAIN 变成任意 SQL 的执行入口。
"""

from __future__ import annotations

from typing import Type

from pydantic import Field
from vanna import Tool, ToolContext, ToolResult
from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.tools.sql_errors import classify_sql_error, error_payload, hint_for_error
from vanna.tools.sql_validation import validate_sql

from analytics_agent.tools.params import ToolArgs

MAX_RESULT_CHARS = 4_000


class ExplainQueryArgs(ToolArgs):
    sql: str = Field(
        min_length=6,
        max_length=4_000,
        description="要解释的 SELECT/WITH 查询（不要带 EXPLAIN 前缀）",
    )


class ExplainQueryTool(Tool[ExplainQueryArgs]):
    """查看 SELECT/WITH 的 ClickHouse 执行计划。

    适用：
    - 大表/多表 join 前，确认扫描行数与分区裁剪；
    - 怀疑全表扫导致超时。

    不适用：
    - 获取业务结果 → run_sql；
    - 非 SELECT/WITH（INSERT/SHOW/EXPLAIN 本身）→ 直接拒绝。

    安全：复用 ``validate_sql`` 做只读 + 单语句校验；10s 服务端超时；结果截断 4,000 字符。
    """

    def __init__(self, runner, timeout_seconds: float = 10.0):
        self._runner = runner
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "explain_query"

    @property
    def description(self) -> str:
        return (
            "explain_query：查看 SELECT/WITH 的 ClickHouse 执行计划。\n"
            "用：大表或多表 join 前确认扫描量；run_sql 超时后判断是否全表扫。\n"
            "不要用：获取业务结果（用 run_sql）、非 SELECT/WITH。\n"
            "分工：先 explain_query 评估 → 再 run_sql 执行。"
        )

    def get_args_schema(self) -> Type[ExplainQueryArgs]:
        return ExplainQueryArgs

    async def _execute(self, sql: str, context: ToolContext):
        settings = {"max_execution_time": self._timeout}
        run_query = getattr(self._runner, "run_query", None)
        if callable(run_query):
            return await run_query(sql, settings=settings)
        return await self._runner.run_sql(RunSqlToolArgs(sql=sql), context)

    async def execute(self, context: ToolContext, args: ExplainQueryArgs) -> ToolResult:
        # 只读 + 单语句：复用 Stage 0 已有的结构化校验
        validation = validate_sql(
            args.sql, read_only=True, max_length=4_000, require_single_statement=True
        )
        if not validation.ok:
            error_type = validation.error_type or "invalid_sql"
            hint = hint_for_error(error_type)
            payload = error_payload(
                error_type, f"EXPLAIN 拒绝执行：{validation.reason}", hint
            )
            return ToolResult(
                success=False,
                result_for_llm=payload,
                error=error_type,
                metadata={"error_type": error_type, "hint": hint},
            )

        # 只允许 SELECT/WITH；EXPLAIN/SHOW 等交给专门工具，避免递归
        if validation.statement_type not in {"SELECT", "WITH"}:
            message = (
                "EXPLAIN 只接受 SELECT/WITH，"
                f"收到 {validation.statement_type or 'unknown'}。"
            )
            hint = "改为 SELECT/WITH；EXPLAIN 本身不用再嵌套。"
            return ToolResult(
                success=False,
                result_for_llm=error_payload("unsupported_statement", message, hint),
                error="unsupported_statement",
                metadata={"error_type": "unsupported_statement", "hint": hint},
            )

        sql = args.sql.strip().rstrip(";")
        explain_sql = f"EXPLAIN PLAN {sql}"

        try:
            frame = await self._execute(explain_sql, context)
        except Exception as exc:
            info = classify_sql_error(exc)
            message = f"EXPLAIN 执行失败：{type(exc).__name__}: {exc}"
            payload = error_payload(info.error_type, message, info.hint)
            return ToolResult(
                success=False,
                result_for_llm=payload,
                error="explain_failed",
                metadata={"error_type": info.error_type, "hint": info.hint},
            )

        text = frame.to_string(index=False, header=False)
        truncated = len(text) > MAX_RESULT_CHARS
        if truncated:
            text = text[:MAX_RESULT_CHARS] + "\n…（执行计划已截断）"

        return ToolResult(
            success=True,
            result_for_llm=(
                f"{text}\n\n"
                "说明：重点看 read_rows / partitions；EXPLAIN 只反映执行计划，"
                "不代表结果一定正确。"
            ),
            metadata={
                "lines": len(frame),
                "truncated": truncated,
            },
        )
