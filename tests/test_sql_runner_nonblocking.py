"""
SQL runner non-blocking tests (P0-C).

验证 RunSqlTool 会把 BLOCKING=True（同步驱动）的 runner 丢到线程池执行，
而 BLOCKING=False（异步驱动）的 runner 仍在主事件循环执行。
"""

import asyncio
import time
from typing import Awaitable, Callable, Tuple

import pandas as pd

from vanna.capabilities.sql_runner import RunSqlToolArgs, SqlRunner
from vanna.core import User
from vanna.core.tool import ToolContext
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.local.file_system import LocalFileSystem
from vanna.integrations.sqlite import SqliteRunner
from vanna.tools import RunSqlTool


def _ctx() -> ToolContext:
    return ToolContext(
        user=User(id="u"),
        conversation_id="c",
        request_id="r",
        agent_memory=DemoAgentMemory(max_items=1),
        metadata={},
    )


class _BlockingRunner(SqlRunner):
    BLOCKING = True

    def __init__(self) -> None:
        self.seen_loop = None

    async def run_sql(self, args, context):
        self.seen_loop = asyncio.get_running_loop()
        return pd.DataFrame({"x": [1]})


class _AsyncRunner(SqlRunner):
    BLOCKING = False

    def __init__(self) -> None:
        self.seen_loop = None

    async def run_sql(self, args, context):
        self.seen_loop = asyncio.get_running_loop()
        return pd.DataFrame({"x": [1]})


class _SlowBlockingRunner(SqlRunner):
    BLOCKING = True

    async def run_sql(self, args, context):
        time.sleep(0.2)
        return pd.DataFrame({"x": [1]})


async def _run_with_ticker(factory: Callable[[], Awaitable[object]]) -> Tuple[object, int]:
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        result = await factory()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return result, ticks


def _tool(runner: SqlRunner, tmp_path) -> RunSqlTool:
    return RunSqlTool(
        sql_runner=runner,
        read_only=True,
        file_system=LocalFileSystem(working_directory=str(tmp_path)),
    )


async def test_blocking_runner_runs_off_loop(tmp_path):
    runner = _BlockingRunner()
    tool = _tool(runner, tmp_path)
    result = await tool.execute(_ctx(), RunSqlToolArgs(sql="SELECT 1"))

    assert result.success is True
    # 同步驱动应被 offload 到独立线程的独立事件循环
    assert runner.seen_loop is not None
    assert runner.seen_loop is not asyncio.get_running_loop()


async def test_async_runner_stays_on_main_loop(tmp_path):
    runner = _AsyncRunner()
    tool = _tool(runner, tmp_path)
    result = await tool.execute(_ctx(), RunSqlToolArgs(sql="SELECT 1"))

    assert result.success is True
    assert runner.seen_loop is asyncio.get_running_loop()


async def test_builtin_sqlite_runner_is_blocking():
    assert SqliteRunner.BLOCKING is True


async def test_blocking_runner_does_not_block_event_loop(tmp_path):
    tool = _tool(_SlowBlockingRunner(), tmp_path)
    result, ticks = await _run_with_ticker(
        lambda: tool.execute(_ctx(), RunSqlToolArgs(sql="SELECT 1"))
    )
    assert result.success is True
    assert ticks >= 5, f"event loop was blocked (ticks={ticks})"
