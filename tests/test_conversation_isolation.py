"""
跨用户会话隔离测试（P0-A）。

覆盖：
- Agent 层：客户端传入他人/不存在的 conversation_id 时，服务端重新生成 id；
- 存储层：update/create 不得跨用户写入，文件后端拒绝路径穿越。
"""

import pytest

from vanna import Agent
from vanna.core import Conversation, Message, User
from vanna.core.registry import ToolRegistry
from vanna.core.user.request_context import RequestContext
from vanna.core.user.resolver import UserResolver
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.local.file_system_conversation_store import (
    FileSystemConversationStore,
)
from vanna.integrations.mock import MockLlmService
from vanna.integrations.sqlite import SQLiteConversationStore
from vanna.servers.base import ChatHandler, ChatRequest


class _HeaderResolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(
            id=request_context.headers.get("x-user", "anon"),
            group_memberships=["user"],
        )


def _agent(store) -> Agent:
    return Agent(
        llm_service=MockLlmService(),
        tool_registry=ToolRegistry(),
        user_resolver=_HeaderResolver(),
        agent_memory=DemoAgentMemory(max_items=10),
        conversation_store=store,
    )


async def _run(handler: ChatHandler, user_id: str, message: str, cid):
    request = ChatRequest(
        message=message,
        conversation_id=cid,
        request_context=RequestContext(
            headers={"x-user": user_id}, cookies={}
        ),
    )
    ids = []
    async for chunk in handler.handle_stream(request):
        if chunk.conversation_id:
            ids.append(chunk.conversation_id)
    return ids


async def test_agent_reassigns_unowned_conversation_id(tmp_path):
    store = SQLiteConversationStore(str(tmp_path / "conv.db"))
    agent = _agent(store)
    handler = ChatHandler(agent)

    # Alice 不传 id，服务端分配
    alice_ids = await _run(handler, "alice", "alice-question", None)
    assert alice_ids
    alice_cid = alice_ids[0]

    # Bob 试图复用 Alice 的 id
    bob_ids = await _run(handler, "bob", "bob-question", alice_cid)
    assert bob_ids
    bob_cid = bob_ids[0]

    assert bob_cid != alice_cid

    alice_conv = await store.get_conversation(alice_cid, User(id="alice"))
    assert alice_conv is not None
    assert all("bob-question" not in (m.content or "") for m in alice_conv.messages)

    assert await store.get_conversation(alice_cid, User(id="bob")) is None
    bob_conv = await store.get_conversation(bob_cid, User(id="bob"))
    assert bob_conv is not None
    store.close()


@pytest.mark.parametrize("backend", ["sqlite", "filesystem"])
async def test_store_rejects_cross_user_update(tmp_path, backend):
    if backend == "sqlite":
        store = SQLiteConversationStore(str(tmp_path / "conv.db"))
        close = store.close
    else:
        store = FileSystemConversationStore(base_dir=str(tmp_path / "convs"))
        close = lambda: None  # noqa: E731

    alice = User(id="alice")
    bob = User(id="bob")

    conv = await store.create_conversation("c1", alice, "hello")
    assert conv.messages[0].content == "hello"

    evil = Conversation(
        id="c1",
        user=bob,
        messages=[Message(role="user", content="evil")],
    )
    with pytest.raises(ValueError):
        await store.update_conversation(evil)

    # Alice 的会话未被污染
    loaded = await store.get_conversation("c1", alice)
    assert loaded is not None
    assert [m.content for m in loaded.messages] == ["hello"]

    # 重复 create 不允许静默覆盖
    with pytest.raises(ValueError):
        await store.create_conversation("c1", bob, "takeover")

    close()


async def test_filesystem_rejects_path_traversal(tmp_path):
    store = FileSystemConversationStore(base_dir=str(tmp_path / "convs"))
    alice = User(id="alice")

    with pytest.raises(ValueError):
        await store.create_conversation("../escape", alice, "x")

    with pytest.raises(ValueError):
        await store.get_conversation("../escape", alice)
