"""Generic SQL query execution tool with dependency injection.

中文说明：通用 SQL 执行工具。通过依赖注入的 SqlRunner 执行 LLM 生成的 SQL，
并在执行前后施加统一护栏（由构造参数配置，而非 LLM 参数，防止被绕过）：
- read_only：只读模式，拒绝 INSERT/UPDATE/DELETE 等写操作
- timeout_seconds：查询超时（秒），超时自动取消
- max_rows：返回行数上限，超出截断
- max_result_chars：喂给 LLM 的结果文本预算，超出截断
"""

from typing import Any, Dict, List, Optional, Type, cast
import asyncio
import uuid

from vanna.core.tool import Tool, ToolContext, ToolResult
from vanna.components import (
    UiComponent,
    DataFrameComponent,
    NotificationComponent,
    ComponentType,
    SimpleTextComponent,
)
from vanna.capabilities.sql_runner import SqlRunner, RunSqlToolArgs
from vanna.capabilities.file_system import FileSystem
from vanna.integrations.local import LocalFileSystem
from .sql_validation import _is_read_only_query, validate_sql

# 喂给 LLM 的结果文本默认预算（字符），超出截断
DEFAULT_MAX_RESULT_CHARS = 1000


def _run_blocking_in_loop(runner, args, context):
    """在独立线程的事件循环里执行同步驱动 runner 的 async run_sql。

    同步数据库驱动（BLOCKING=True）的 run_sql 若直接 await，会阻塞主事件循环；
    这里把它丢进线程池，并在该线程内用独立事件循环跑完这个协程。异步驱动实现
    （BLOCKING=False）不会走到这里。
    """
    return asyncio.run(runner.run_sql(args, context))


class RunSqlTool(Tool[RunSqlToolArgs]):
    """Tool that executes SQL queries using an injected SqlRunner implementation.

    中文说明：SQL 执行工具，内置企业级护栏。护栏通过构造参数配置（而非 LLM 参数），
    从而在服务端强制生效、无法被模型绕过。
    """

    def __init__(
        self,
        sql_runner: SqlRunner,
        file_system: Optional[FileSystem] = None,
        custom_tool_name: Optional[str] = None,
        custom_tool_description: Optional[str] = None,
        read_only: bool = True,
        timeout_seconds: Optional[float] = None,
        max_rows: Optional[int] = 1000,
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
        max_sql_length: Optional[int] = None,
        require_single_statement: bool = True,
    ):
        """Initialize the tool with a SqlRunner implementation.

        Args:
            sql_runner: SqlRunner implementation that handles actual query execution
            file_system: FileSystem implementation for saving results (defaults to LocalFileSystem)
            custom_tool_name: Optional custom name for the tool (overrides default "run_sql")
            custom_tool_description: Optional custom description for the tool (overrides default description)
            read_only: 只读模式，True 时拒绝写操作（默认 True，安全优先；确需写入
                时显式置 False）
            timeout_seconds: 查询超时秒数（None 表示不限）。注意：对同步驱动
                （SQLite/PostgreSQL/Snowflake 等）asyncio.wait_for 无法中断阻塞线程，
                真正的超时需要这些 runner 用数据库原生的 statement_timeout 配合。
            max_rows: 返回行数上限（默认 1000，防止大结果集打满内存/SSE；None 表示不限）
            max_result_chars: 喂给 LLM 的结果文本预算（默认 1000）
            max_sql_length: SQL 最大字符数（None 表示不限），防御超长/注入型内容
            require_single_statement: 是否只允许单条语句（默认 True，拒绝堆叠语句）
        """
        self.sql_runner = sql_runner
        self.file_system = file_system or LocalFileSystem()
        self._custom_name = custom_tool_name
        self._custom_description = custom_tool_description
        self.read_only = read_only
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.max_result_chars = max_result_chars
        self.max_sql_length = max_sql_length
        self.require_single_statement = require_single_statement

    def _run_sql(self, args: RunSqlToolArgs, context: ToolContext):
        """执行 SQL；同步驱动（BLOCKING=True）放到线程池，避免阻塞事件循环。"""
        runner = self.sql_runner
        if getattr(runner, "BLOCKING", False):
            return asyncio.to_thread(_run_blocking_in_loop, runner, args, context)
        return runner.run_sql(args, context)

    @property
    def name(self) -> str:
        return self._custom_name if self._custom_name else "run_sql"

    @property
    def description(self) -> str:
        return (
            self._custom_description
            if self._custom_description
            else "Execute SQL queries against the configured database"
        )

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(self, context: ToolContext, args: RunSqlToolArgs) -> ToolResult:
        """Execute a SQL query using the injected SqlRunner with guardrails.

        中文说明：执行前做只读校验，执行时套查询超时，执行后按 max_rows /
        max_result_chars 截断。
        """
        # 结构化校验：空语句 / 多语句（堆叠注入）/ 超长 / 只读违规。
        # 校验失败时把结构化原因回喂 LLM，让其在下一轮自我修复。
        validation = validate_sql(
            args.sql,
            read_only=self.read_only,
            max_length=self.max_sql_length,
            require_single_statement=self.require_single_statement,
        )
        if not validation.ok:
            message = validation.reason or "SQL validation failed."
            return ToolResult(
                success=False,
                result_for_llm=message,
                ui_component=UiComponent(
                    rich_component=NotificationComponent(
                        type=ComponentType.NOTIFICATION,
                        level="error",
                        message=message,
                    ),
                    simple_component=SimpleTextComponent(text=message),
                ),
                error=message,
                metadata={"error_type": validation.error_type or "invalid_sql"},
            )

        # 执行查询（套超时；同步驱动走线程池，见 _run_sql）
        try:
            run = self._run_sql(args, context)
            if self.timeout_seconds is not None:
                df = await asyncio.wait_for(run, timeout=self.timeout_seconds)
            else:
                df = await run
        except (TimeoutError, asyncio.TimeoutError) as e:
            # str(e) 为空时（asyncio.wait_for 触发）回退到配置值
            message = (
                str(e).strip()
                or f"Query timed out after {self.timeout_seconds} seconds."
            )
            return ToolResult(
                success=False,
                result_for_llm=message,
                ui_component=UiComponent(
                    rich_component=NotificationComponent(
                        type=ComponentType.NOTIFICATION,
                        level="error",
                        message=message,
                    ),
                    simple_component=SimpleTextComponent(text=message),
                ),
                error=message,
                metadata={"error_type": "query_timeout"},
            )

        try:
            # 行数上限截断（在转 records / 写 CSV 之前统一处理）
            total_rows = len(df)
            rows_truncated = False
            if self.max_rows is not None and len(df) > self.max_rows:
                df = df.head(self.max_rows)
                rows_truncated = True

            # Determine query type
            query_type = args.sql.strip().upper().split()[0]

            if query_type == "SELECT":
                # Handle SELECT queries with results
                if df.empty:
                    result = "Query executed successfully. No rows returned."
                    ui_component = UiComponent(
                        rich_component=DataFrameComponent(
                            rows=[],
                            columns=[],
                            title="Query Results",
                            description="No rows returned",
                        ),
                        simple_component=SimpleTextComponent(text=result),
                    )
                    metadata = {
                        "row_count": 0,
                        "columns": [],
                        "query_type": query_type,
                        "results": [],
                    }
                else:
                    # Convert DataFrame to records
                    results_data = df.to_dict("records")
                    columns = df.columns.tolist()
                    row_count = len(df)

                    # Write DataFrame to CSV file for downstream tools
                    file_id = str(uuid.uuid4())[:8]
                    filename = f"query_results_{file_id}.csv"
                    csv_content = df.to_csv(index=False)
                    await self.file_system.write_file(
                        filename, csv_content, context, overwrite=True
                    )

                    # Create result text for LLM with truncated results
                    results_preview = csv_content
                    if len(results_preview) > self.max_result_chars:
                        results_preview = (
                            results_preview[: self.max_result_chars]
                            + f"\n(Results truncated to {self.max_result_chars} characters. "
                            "FOR LARGE RESULTS YOU DO NOT NEED TO SUMMARIZE THESE RESULTS "
                            "OR PROVIDE OBSERVATIONS. THE NEXT STEP SHOULD BE A "
                            "VISUALIZE_DATA CALL)"
                        )

                    result = (
                        f"{results_preview}\n\nResults saved to file: {filename}\n\n"
                        f"**IMPORTANT: FOR VISUALIZE_DATA USE FILENAME: {filename}**"
                    )

                    # Create DataFrame component for UI
                    dataframe_component = DataFrameComponent.from_records(
                        records=cast(List[Dict[str, Any]], results_data),
                        title="Query Results",
                        description=f"SQL query returned {row_count} rows with {len(columns)} columns",
                    )

                    ui_component = UiComponent(
                        rich_component=dataframe_component,
                        simple_component=SimpleTextComponent(text=result),
                    )

                    metadata = {
                        "row_count": row_count,
                        "columns": columns,
                        "query_type": query_type,
                        "results": results_data,
                        "output_file": filename,
                        "total_rows": total_rows,
                        "rows_truncated": rows_truncated,
                    }
            else:
                # For non-SELECT queries (INSERT, UPDATE, DELETE, etc.)
                # The SqlRunner should return a DataFrame with affected row count
                rows_affected = len(df) if not df.empty else 0
                result = (
                    f"Query executed successfully. {rows_affected} row(s) affected."
                )

                metadata = {"rows_affected": rows_affected, "query_type": query_type}
                ui_component = UiComponent(
                    rich_component=NotificationComponent(
                        type=ComponentType.NOTIFICATION, level="success", message=result
                    ),
                    simple_component=SimpleTextComponent(text=result),
                )

            return ToolResult(
                success=True,
                result_for_llm=result,
                ui_component=ui_component,
                metadata=metadata,
            )

        except Exception as e:
            error_message = f"Error executing query: {str(e)}"
            return ToolResult(
                success=False,
                result_for_llm=error_message,
                ui_component=UiComponent(
                    rich_component=NotificationComponent(
                        type=ComponentType.NOTIFICATION,
                        level="error",
                        message=error_message,
                    ),
                    simple_component=SimpleTextComponent(text=error_message),
                ),
                error=str(e),
                metadata={"error_type": "sql_error"},
            )
