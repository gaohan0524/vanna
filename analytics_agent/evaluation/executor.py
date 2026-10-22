"""Read-only SQL execution for evaluation.

`EvaluationRunner` 只采集 `tool_calls`（工具名 + 参数），不采集工具执行结果。
因此评测侧必须把 agent 生成的 SQL 重放一遍，再与 golden SQL 的结果比较。

只读由两件事保证：
1. 连接使用只读账号（仅 GRANT SELECT）；
2. 查询级 settings 限制超时与返回行数。
"""

from __future__ import annotations

import asyncio
from typing import Optional, Tuple

import clickhouse_connect
import pandas as pd

from analytics_agent.config import Settings

_CLIENT = None
_CLIENT_KEY: Optional[Tuple[str, int, str, str]] = None


def get_client(settings: Optional[Settings] = None):
    """Return a cached ClickHouse client for the query account."""
    global _CLIENT, _CLIENT_KEY

    s = settings or Settings.from_env()
    key = (s.ch_host, s.ch_port, s.ch_database, s.ch_user)
    if _CLIENT is None or key != _CLIENT_KEY:
        _CLIENT = clickhouse_connect.get_client(
            host=s.ch_host,
            port=s.ch_port,
            database=s.ch_database,
            username=s.ch_user,
            password=s.ch_password,
        )
        _CLIENT_KEY = key
    return _CLIENT


def _query_sync(sql: str, timeout_s: int, max_rows: int) -> pd.DataFrame:
    client = get_client()
    result = client.query(
        sql,
        settings={
            "max_execution_time": timeout_s,
            "max_result_rows": max_rows,
            "result_overflow_mode": "break",
        },
    )
    return pd.DataFrame(result.result_rows, columns=result.column_names)


async def execute_readonly(
    sql: str,
    *,
    timeout_s: Optional[int] = None,
    max_rows: Optional[int] = None,
    settings: Optional[Settings] = None,
) -> pd.DataFrame:
    """Execute one read-only SQL statement and return a DataFrame.

    clickhouse-connect 是同步驱动，放到线程池里跑，避免阻塞事件循环。
    异常原样抛出，由调用方决定是“该用例失败”还是“评测环境故障”。
    """
    s = settings or Settings.from_env()
    return await asyncio.to_thread(
        _query_sync,
        sql,
        timeout_s if timeout_s is not None else s.ch_timeout_s,
        max_rows if max_rows is not None else s.ch_max_rows,
    )


async def execute_safe(
    sql: str,
    *,
    timeout_s: Optional[int] = None,
    max_rows: Optional[int] = None,
    settings: Optional[Settings] = None,
) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """Like :func:`execute_readonly`, but returns ``(df, error)`` instead of raising."""
    try:
        return (
            await execute_readonly(
                sql, timeout_s=timeout_s, max_rows=max_rows, settings=settings
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001 - 评测需要保留任意错误信息
        return None, f"{type(exc).__name__}: {exc}"
