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
import re
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

# 喂给 LLM 的结果文本默认预算（字符），超出截断
DEFAULT_MAX_RESULT_CHARS = 1000

# 只读模式允许的首关键字
_READ_ONLY_LEADING_KEYWORDS = {
    "SELECT",
    "WITH",
    "EXPLAIN",
    "SHOW",
    "DESCRIBE",
    "DESC",
    "PRAGMA",
}

# 显式禁止的写操作关键字（防御 WITH ... INSERT 或子查询混入 DML）
_WRITE_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "MERGE",
    "GRANT",
    "REVOKE",
    "REPLACE",
    "UPSERT",
    "CALL",
    "EXEC",
    "EXECUTE",
    "VACUUM",
    "ATTACH",
    "DETACH",
    "COMMIT",
    "ROLLBACK",
    "BEGIN",
}


def _strip_sql_comments(sql: str) -> str:
    """移除 SQL 中的块注释与行注释，便于关键字判定。"""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _is_read_only_query(sql: str) -> bool:
    """判断 SQL 是否为只读查询（首关键字属于只读白名单且全文不含写关键字）。"""
    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        return False

    leading = cleaned.split(None, 1)[0].upper()
    if leading not in _READ_ONLY_LEADING_KEYWORDS:
        return False

    upper = cleaned.upper()
    return not any(re.search(rf"\b{kw}\b", upper) for kw in _WRITE_KEYWORDS)


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
        read_only: bool = False,
        timeout_seconds: Optional[float] = None,
        max_rows: Optional[int] = None,
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    ):
        """Initialize the tool with a SqlRunner implementation.

        Args:
            sql_runner: SqlRunner implementation that handles actual query execution
            file_system: FileSystem implementation for saving results (defaults to LocalFileSystem)
            custom_tool_name: Optional custom name for the tool (overrides default "run_sql")
            custom_tool_description: Optional custom description for the tool (overrides default description)
            read_only: 只读模式，True 时拒绝写操作（默认 False）
            timeout_seconds: 查询超时秒数（None 表示不限）。注意：对同步驱动
                （SQLite/PostgreSQL/Snowflake 等）asyncio.wait_for 无法中断阻塞线程，
                真正的超时需要这些 runner 用数据库原生的 statement_timeout 配合。
            max_rows: 返回行数上限（None 表示不限）
            max_result_chars: 喂给 LLM 的结果文本预算（默认 1000）
        """
        self.sql_runner = sql_runner
        self.file_system = file_system or LocalFileSystem()
        self._custom_name = custom_tool_name
        self._custom_description = custom_tool_description
        self.read_only = read_only
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.max_result_chars = max_result_chars

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
        # 只读模式校验：在执行前拦截写操作
        if self.read_only and not _is_read_only_query(args.sql):
            message = (
                "Write operations are not allowed in read-only mode. "
                f"Rejected query: {args.sql.strip()[:200]}"
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
                metadata={"error_type": "read_only_violation"},
            )

        # 执行查询（套超时）
        try:
            if self.timeout_seconds is not None:
                df = await asyncio.wait_for(
                    self.sql_runner.run_sql(args, context),
                    timeout=self.timeout_seconds,
                )
            else:
                df = await self.sql_runner.run_sql(args, context)
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
