"""业务侧 run_sql：在框架 RunSqlTool 外面加 AST 校验与 LIMIT 注入。"""

from __future__ import annotations

from typing import Type

from vanna import ToolContext, ToolResult
from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.tools import RunSqlTool
from vanna.tools.sql_errors import error_payload, hint_for_error

from analytics_agent.tools.guardrails import SqlGuardError, prepare_sql


class AnalyticsRunSqlTool(RunSqlTool):
    """只读 SQL 工具。

    适用：
    - 表、字段、口径都已确定，需要拿结果。

    不适用：
    - 猜字段（search_columns）、确认取值（sample_rows）、查口径（get_metric_definition）。

    在框架 RunSqlTool 之上增加：
    - sqlglot AST 只读校验（字符串里的 `'delete'` 不再误杀）；
    - 无 LIMIT 时自动包一层 `SELECT * FROM (...) LIMIT n`；
    - 护栏拒绝时返回结构化 error_type，供模型自我修复。
    """

    def __init__(self, *, auto_limit: int = 1000, **kwargs) -> None:
        super().__init__(**kwargs)
        self.auto_limit = auto_limit

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(self, context: ToolContext, args: RunSqlToolArgs) -> ToolResult:
        try:
            prepared_sql, note = prepare_sql(args.sql, limit=self.auto_limit)
        except SqlGuardError as exc:
            hint = hint_for_error(exc.error_type)
            payload = error_payload(exc.error_type, exc.message, hint)
            return ToolResult(
                success=False,
                result_for_llm=payload,
                error=exc.error_type,
                metadata={"error_type": exc.error_type, "hint": hint},
            )

        result = await super().execute(
            context, args.model_copy(update={"sql": prepared_sql})
        )
        if note and result.success:
            result.result_for_llm = f"{note}\n\n{result.result_for_llm}"
            result.metadata = {**(result.metadata or {}), "auto_limit": self.auto_limit}
        return result
