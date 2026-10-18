"""
Local file I/O must not block the event loop.

验证 `LocalFileSystem` 与 `FileSystemConversationStore` 的磁盘 I/O 已下沉到
`asyncio.to_thread`：即使底层读写很慢，事件循环仍能继续调度其它协程。
"""

import asyncio
import time
from typing import Awaitable, Callable, Tuple

import pytest

from vanna.core import Message, User
from vanna.core.tool import ToolContext
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.local.file_system import LocalFileSystem
from vanna.integrations.local.file_system_conversation_store import (
    FileSystemConversationStore,
)


def _context(user_id: str = "u") -> ToolContext:
    return ToolContext(
        user=User(id=user_id),
        conversation_id="c",
        request_id="r",
        agent_memory=DemoAgentMemory(max_items=1),
        metadata={},
    )


async def _run_with_ticker(
    factory: Callable[[], Awaitable[object]],
) -> Tuple[object, int]:
    """Run a coroutine while a ticker counts event-loop iterations."""
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


# --------------------------------------------------------------------------- #
# LocalFileSystem
# --------------------------------------------------------------------------- #
async def test_local_file_system_roundtrip(tmp_path):
    fs = LocalFileSystem(working_directory=str(tmp_path))
    ctx = _context()

    await fs.write_file("a.txt", "hello world", ctx)
    assert await fs.exists("a.txt", ctx) is True
    assert await fs.is_directory("a.txt", ctx) is False
    assert await fs.read_file("a.txt", ctx) == "hello world"
    assert await fs.list_files(".", ctx) == ["a.txt"]

    matches = await fs.search_files("hello", ctx, include_content=True)
    assert matches
    assert matches[0].path == "a.txt"

    with pytest.raises(FileExistsError):
        await fs.write_file("a.txt", "x", ctx)

    await fs.write_file("a.txt", "x", ctx, overwrite=True)
    assert await fs.read_file("a.txt", ctx) == "x"


async def test_read_file_does_not_block_event_loop(tmp_path, monkeypatch):
    fs = LocalFileSystem(working_directory=str(tmp_path))
    ctx = _context()

    def slow_read(self, filename, context):
        time.sleep(0.2)
        return "ok"

    monkeypatch.setattr(LocalFileSystem, "_read_file_sync", slow_read)

    result, ticks = await _run_with_ticker(lambda: fs.read_file("a.txt", ctx))
    assert result == "ok"
    assert ticks >= 5, f"event loop was blocked (ticks={ticks})"


async def test_search_files_does_not_block_event_loop(tmp_path, monkeypatch):
    fs = LocalFileSystem(working_directory=str(tmp_path))
    ctx = _context()

    def slow_search(self, query, context, max_results, include_content):
        time.sleep(0.2)
        return []

    monkeypatch.setattr(LocalFileSystem, "_search_files_sync", slow_search)

    result, ticks = await _run_with_ticker(
        lambda: fs.search_files("x", ctx, include_content=True)
    )
    assert result == []
    assert ticks >= 5, f"event loop was blocked (ticks={ticks})"


# --------------------------------------------------------------------------- #
# FileSystemConversationStore
# --------------------------------------------------------------------------- #
async def test_file_conversation_store_roundtrip(tmp_path):
    store = FileSystemConversationStore(base_dir=str(tmp_path / "convs"))
    alice = User(id="alice")
    bob = User(id="bob")

    conv = await store.create_conversation("c1", alice, "hello")
    assert conv.messages[0].content == "hello"

    loaded = await store.get_conversation("c1", alice)
    assert loaded is not None
    loaded.add_message(Message(role="assistant", content="hi"))
    await store.update_conversation(loaded)

    again = await store.get_conversation("c1", alice)
    assert [m.content for m in again.messages] == ["hello", "hi"]

    # 用户隔离
    assert await store.get_conversation("c1", bob) is None
    assert await store.list_conversations(bob) == []
    assert len(await store.list_conversations(alice)) == 1

    assert await store.delete_conversation("c1", alice) is True
    assert await store.get_conversation("c1", alice) is None


async def test_get_conversation_does_not_block_event_loop(tmp_path, monkeypatch):
    store = FileSystemConversationStore(base_dir=str(tmp_path / "convs"))
    alice = User(id="alice")

    def slow_get(self, conversation_id, user):
        time.sleep(0.2)
        return None

    monkeypatch.setattr(
        FileSystemConversationStore, "_get_conversation_sync", slow_get
    )

    result, ticks = await _run_with_ticker(
        lambda: store.get_conversation("c1", alice)
    )
    assert result is None
    assert ticks >= 5, f"event loop was blocked (ticks={ticks})"
