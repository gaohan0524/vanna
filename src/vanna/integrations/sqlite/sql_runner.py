"""SQLite implementation of SqlRunner interface.

中文说明：SQLite 的 SqlRunner 实现。SQLite 驱动是同步的，故这里用 progress_handler
实现原生查询超时——每执行 N 条 VM 指令检查一次截止时间，超时抛 TimeoutError 中断。
"""

import sqlite3
import time
from typing import Optional

import pandas as pd

from vanna.capabilities.sql_runner import SqlRunner, RunSqlToolArgs
from vanna.core.tool import ToolContext


class SqliteRunner(SqlRunner):
    """SQLite implementation of the SqlRunner interface."""

    BLOCKING = True  # sqlite3 是同步驱动，由 RunSqlTool 放到线程池执行

    def __init__(self, database_path: str, timeout_seconds: Optional[float] = None):
        """Initialize with a SQLite database path.

        Args:
            database_path: Path to the SQLite database file
            timeout_seconds: 查询超时秒数（None 表示不限），通过 progress_handler 实现
        """
        self.database_path = database_path
        self.timeout_seconds = timeout_seconds

    async def run_sql(self, args: RunSqlToolArgs, context: ToolContext) -> pd.DataFrame:
        """Execute SQL query against SQLite database and return results as DataFrame.

        Args:
            args: SQL query arguments
            context: Tool execution context

        Returns:
            DataFrame with query results

        Raises:
            sqlite3.Error: If query execution fails
            TimeoutError: If the query exceeds timeout_seconds
        """
        # Connect to the database
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        cursor = conn.cursor()

        # 原生查询超时：progress_handler 每 1000 条 VM 指令检查一次截止时间，
        # 返回非零值中断查询（sqlite3 C 层会抛 OperationalError("interrupted")）
        deadline: Optional[float] = None
        if self.timeout_seconds is not None:
            deadline = time.monotonic() + self.timeout_seconds

            def _check_deadline() -> int:
                return 1 if time.monotonic() > deadline else 0

            conn.set_progress_handler(_check_deadline, 1000)

        try:
            # Execute the query
            cursor.execute(args.sql)

            # Determine if this is a SELECT query or modification query
            query_type = args.sql.strip().upper().split()[0]

            if query_type == "SELECT":
                # Fetch results for SELECT queries
                rows = cursor.fetchall()
                if not rows:
                    # Return empty DataFrame
                    return pd.DataFrame()

                # Convert rows to list of dictionaries
                results_data = [dict(row) for row in rows]
                return pd.DataFrame(results_data)
            else:
                # For non-SELECT queries (INSERT, UPDATE, DELETE, etc.)
                conn.commit()
                rows_affected = cursor.rowcount
                # Return a DataFrame indicating rows affected
                return pd.DataFrame({"rows_affected": [rows_affected]})

        except sqlite3.OperationalError as e:
            # 超时中断：interrupted 且已过截止时间 → 统一转 TimeoutError
            if (
                deadline is not None
                and "interrupted" in str(e).lower()
                and time.monotonic() > deadline
            ):
                raise TimeoutError(
                    f"Query timed out after {self.timeout_seconds} seconds"
                ) from e
            raise

        finally:
            cursor.close()
            conn.close()
