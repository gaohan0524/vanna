"""
SQL runner capability interface.

This module contains the abstract base class for SQL execution.

本模块定义 SQL 执行能力的抽象接口 SqlRunner，是 Text2SQL 链路中「真正执行 SQL」的
抽象边界：RunSqlTool 注入一个 SqlRunner 实现（SQLite/PostgreSQL/ClickHouse 等），
把 LLM 生成的 SQL 交给它执行并取回 pandas.DataFrame。换数据库只需实现本接口。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import pandas as pd

from .models import RunSqlToolArgs

if TYPE_CHECKING:
    from vanna.core.tool import ToolContext


class SqlRunner(ABC):
    """Interface for SQL execution with different implementations.

    SQL 执行接口：不同数据库（SQLite/Postgres/ClickHouse 等）各自实现 run_sql。

    BLOCKING 类属性：标记实现是否使用同步数据库驱动。同步驱动的 run_sql 若直接在
    事件循环里执行会阻塞整条循环；RunSqlTool 检测到 BLOCKING=True 时会把它丢到
    线程池（asyncio.to_thread）执行。异步驱动（如 asyncpg）的实现保持 False。
    """

    # 同步驱动实现置 True；异步驱动实现保持 False
    BLOCKING: bool = False

    @abstractmethod
    async def run_sql(
        self, args: RunSqlToolArgs, context: "ToolContext"
    ) -> pd.DataFrame:
        """Execute SQL query and return results as a DataFrame.

        Args:
            args: SQL query arguments
            context: Tool execution context

        Returns:
            DataFrame with query results

        Raises:
            Exception: If query execution fails

        执行 SQL 并返回 DataFrame。Text2SQL 的核心执行点：args.sql 是 LLM 生成的
        语句，返回的 DataFrame 由 RunSqlTool 包装成表格组件展示给用户。
        """
        pass
