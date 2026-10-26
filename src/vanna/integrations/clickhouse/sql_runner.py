"""ClickHouse implementation of SqlRunner interface."""

from typing import Any, Dict, Optional

import pandas as pd

from vanna.capabilities.sql_runner import SqlRunner, RunSqlToolArgs
from vanna.core.tool import ToolContext


class ClickHouseRunner(SqlRunner):
    """ClickHouse implementation of the SqlRunner interface."""

    BLOCKING = True  # clickhouse-connect 是同步驱动，由 RunSqlTool 放到线程池执行

    def __init__(
        self,
        host: str,
        database: str,
        user: str,
        password: str,
        port: int = 8123,
        query_settings: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """Initialize with ClickHouse connection parameters.

        Args:
            host: Database host address
            database: Database name
            user: Database user
            password: Database password
            port: Database port (default: 8123)
            **kwargs: Additional clickhouse_connect connection parameters
        """
        try:
            import clickhouse_connect

            self.clickhouse_connect = clickhouse_connect
        except ImportError as e:
            raise ImportError(
                "clickhouse-connect package is required. "
                "Install with: pip install 'vanna[clickhouse]'"
            ) from e

        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.kwargs = kwargs
        # 默认查询设置：例如 {"max_execution_time": 30}。
        # 同步驱动在线程里跑，asyncio.wait_for 打不断，必须让 ClickHouse 自己超时。
        self.query_settings = query_settings or {}

    async def run_query(
        self, sql: str, settings: Optional[Dict[str, Any]] = None
    ) -> pd.DataFrame:
        """执行一条 SQL 并返回 DataFrame。

        ``settings`` 会透传给 clickhouse-connect 的 ``client.query``，用于
        ``max_execution_time`` 等服务端设置；缺省使用构造时的 ``query_settings``。
        """
        client = self.clickhouse_connect.get_client(
            host=self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            database=self.database,
            **self.kwargs,
        )

        try:
            result = client.query(
                sql,
                settings=self.query_settings if settings is None else settings,
            )
            return pd.DataFrame(result.result_rows, columns=result.column_names)
        finally:
            client.close()

    async def run_sql(self, args: RunSqlToolArgs, context: ToolContext) -> pd.DataFrame:
        """SqlRunner 接口实现：复用 ``run_query`` 的默认 settings。"""
        return await self.run_query(args.sql)
