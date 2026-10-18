"""
Persistence + multi-tenant isolation tests.

验证新增的 SQLite 持久化后端（会话存储 / 智能体记忆）能跨实例读回数据，并正确按
user / tenant 隔离；同时确认 DemoAgentMemory 的默认共享行为不变，且可选开启隔离。
"""

import pytest

from vanna.core import Conversation, Message, User
from vanna.core.tool import ToolContext
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.sqlite import (
    SQLiteAgentMemory,
    SQLiteConversationStore,
)


def _ctx(user: User, memory) -> ToolContext:
    return ToolContext(
        user=user,
        conversation_id="c",
        request_id="r",
        agent_memory=memory,
        metadata={},
    )


# --------------------------------------------------------------------------- #
# SQLiteConversationStore
# --------------------------------------------------------------------------- #
async def test_conversation_store_roundtrip_and_persistence(tmp_path):
    db = str(tmp_path / "conv.db")
    store = SQLiteConversationStore(db)
    alice = User(id="alice")

    conv = await store.create_conversation("c1", alice, "hello")
    assert conv.messages[0].content == "hello"

    loaded = await store.get_conversation("c1", alice)
    assert loaded is not None
    loaded.add_message(Message(role="assistant", content="hi"))
    await store.update_conversation(loaded)

    again = await store.get_conversation("c1", alice)
    assert [m.content for m in again.messages] == ["hello", "hi"]
    assert len(await store.list_conversations(alice)) == 1

    # 持久化：新建实例仍能读回同一份数据
    store.close()
    store2 = SQLiteConversationStore(db)
    reloaded = await store2.get_conversation("c1", alice)
    assert [m.content for m in reloaded.messages] == ["hello", "hi"]
    store2.close()


async def test_conversation_store_user_isolation(tmp_path):
    store = SQLiteConversationStore(str(tmp_path / "conv.db"))
    alice = User(id="alice")
    bob = User(id="bob")

    await store.create_conversation("c1", alice, "alice secret")

    # 他人不可见、不可删
    assert await store.get_conversation("c1", bob) is None
    assert await store.list_conversations(bob) == []
    assert await store.delete_conversation("c1", bob) is False
    assert await store.get_conversation("c1", alice) is not None
    store.close()


async def test_conversation_store_tenant_sharing(tmp_path):
    store = SQLiteConversationStore(
        str(tmp_path / "conv.db"),
        tenant_resolver=lambda u: u.metadata.get("org", u.id),
        share_within_tenant=True,
    )
    alice = User(id="alice", metadata={"org": "acme"})
    bob = User(id="bob", metadata={"org": "acme"})
    carol = User(id="carol", metadata={"org": "other"})

    await store.create_conversation("c1", alice, "shared")

    assert await store.get_conversation("c1", bob) is not None
    assert await store.get_conversation("c1", carol) is None
    store.close()


async def test_conversation_store_delete_cascades_messages(tmp_path):
    db = str(tmp_path / "conv.db")
    store = SQLiteConversationStore(db)
    alice = User(id="alice")
    await store.create_conversation("c1", alice, "hello")
    assert await store.delete_conversation("c1", alice) is True
    assert await store.get_conversation("c1", alice) is None
    store.close()


# --------------------------------------------------------------------------- #
# SQLiteAgentMemory
# --------------------------------------------------------------------------- #
async def test_agent_memory_roundtrip_and_persistence(tmp_path):
    db = str(tmp_path / "mem.db")
    memory = SQLiteAgentMemory(db)
    alice = User(id="alice")
    ctx = _ctx(alice, memory)

    await memory.save_tool_usage(
        "show top customers", "run_sql", {"sql": "select * from customers"}, ctx
    )

    results = await memory.search_similar_usage(
        "show best customers", ctx, similarity_threshold=0.3
    )
    assert results
    assert results[0].memory.question == "show top customers"
    assert results[0].memory.user_id == "alice"

    recent = await memory.get_recent_memories(ctx)
    assert len(recent) == 1

    # 持久化：新实例仍能检索到
    memory.close()
    memory2 = SQLiteAgentMemory(db)
    ctx2 = _ctx(alice, memory2)
    results2 = await memory2.search_similar_usage(
        "show best customers", ctx2, similarity_threshold=0.3
    )
    assert results2 and results2[0].memory.question == "show top customers"
    memory2.close()


async def test_agent_memory_user_isolation(tmp_path):
    memory = SQLiteAgentMemory(str(tmp_path / "mem.db"))
    alice = User(id="alice")
    bob = User(id="bob")
    ca, cb = _ctx(alice, memory), _ctx(bob, memory)

    await memory.save_tool_usage("alice question", "run_sql", {}, ca)

    # bob 看不到 alice 的记忆
    assert await memory.search_similar_usage(
        "alice question", cb, similarity_threshold=0.0
    ) == []
    assert await memory.get_recent_memories(cb) == []
    # alice 自己仍可见
    assert len(await memory.get_recent_memories(ca)) == 1
    memory.close()


async def test_agent_memory_tenant_sharing(tmp_path):
    memory = SQLiteAgentMemory(
        str(tmp_path / "mem.db"),
        tenant_resolver=lambda u: u.metadata.get("org", u.id),
        share_within_tenant=True,
    )
    alice = User(id="alice", metadata={"org": "acme"})
    bob = User(id="bob", metadata={"org": "acme"})
    ca, cb = _ctx(alice, memory), _ctx(bob, memory)

    await memory.save_tool_usage("shared question", "run_sql", {}, ca)

    results = await memory.search_similar_usage(
        "shared question", cb, similarity_threshold=0.3
    )
    assert results and results[0].memory.question == "shared question"
    memory.close()


async def test_agent_memory_text_memory_and_delete(tmp_path):
    memory = SQLiteAgentMemory(str(tmp_path / "mem.db"))
    alice = User(id="alice")
    ctx = _ctx(alice, memory)

    text = await memory.save_text_memory("orders table has amount column", ctx)
    assert text.user_id == "alice"

    found = await memory.search_text_memories(
        "orders amount column", ctx, similarity_threshold=0.3
    )
    assert found and found[0].memory.content.startswith("orders table")

    assert await memory.delete_text_memory(ctx, text.memory_id) is True
    assert await memory.get_recent_text_memories(ctx) == []
    memory.close()


async def test_agent_memory_clear_is_scoped(tmp_path):
    memory = SQLiteAgentMemory(str(tmp_path / "mem.db"))
    alice = User(id="alice")
    bob = User(id="bob")
    ca, cb = _ctx(alice, memory), _ctx(bob, memory)

    await memory.save_tool_usage("a1", "run_sql", {}, ca)
    await memory.save_tool_usage("a2", "run_sql", {}, ca)
    await memory.save_tool_usage("b1", "run_sql", {}, cb)

    # alice 仅清空自己的 2 条
    assert await memory.clear_memories(ca) == 2
    assert await memory.get_recent_memories(ca) == []
    assert len(await memory.get_recent_memories(cb)) == 1
    memory.close()


# --------------------------------------------------------------------------- #
# DemoAgentMemory：默认共享行为不变，可选开启隔离
# --------------------------------------------------------------------------- #
async def test_demo_memory_default_still_shared():
    memory = DemoAgentMemory()
    alice = User(id="alice")
    bob = User(id="bob")
    ca, cb = _ctx(alice, memory), _ctx(bob, memory)

    await memory.save_tool_usage("count records", "run_sql", {}, ca)

    # 默认（user_scoped=False）保持旧的共享语义，避免破坏既有 demo/测试
    results = await memory.search_similar_usage(
        "count records", cb, similarity_threshold=0.3
    )
    assert results


async def test_demo_memory_user_scoped_isolation():
    memory = DemoAgentMemory(user_scoped=True)
    alice = User(id="alice")
    bob = User(id="bob")
    ca, cb = _ctx(alice, memory), _ctx(bob, memory)

    await memory.save_tool_usage("alice question", "run_sql", {}, ca)

    assert await memory.search_similar_usage(
        "alice question", cb, similarity_threshold=0.0
    ) == []
    assert await memory.get_recent_memories(cb) == []
    assert len(await memory.get_recent_memories(ca)) == 1
