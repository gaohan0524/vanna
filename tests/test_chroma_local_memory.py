"""Smoke tests for the app-side ChromaLocalMemory backend."""

from __future__ import annotations

import asyncio
import hashlib
from typing import List

import pytest
from vanna import User
from vanna.core.tool import ToolContext

from analytics_agent.memory.backends.chroma_local import ChromaLocalMemory


class _TestEmbedding:
    """Small deterministic Chinese-friendly embedding for offline smoke tests."""

    def __init__(self, dimension: int = 64, name: str = "test-char-hash-v1") -> None:
        self.dimension = dimension
        self._name = name

    def name(self) -> str:
        return self._name

    def __call__(self, input: List[str]) -> List[List[float]]:
        vectors = []
        for text in input:
            vector = [0.0] * self.dimension
            for char in text:
                bucket = hashlib.sha256(char.encode("utf-8")).digest()[0]
                vector[bucket % self.dimension] += 1.0
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            vectors.append([value / norm for value in vector])
        return vectors

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        return self(input)

    def embed_query(self, input: List[str]) -> List[List[float]]:
        return self(input)


def _context(
    user_id: str,
    tenant_id: str | None = None,
    memory: ChromaLocalMemory | None = None,
) -> ToolContext:
    metadata = {"tenant_id": tenant_id} if tenant_id else {}
    user = User(id=user_id, metadata=metadata)
    return ToolContext(
        user=user,
        conversation_id="test",
        request_id="test",
        agent_memory=memory,  # type: ignore[arg-type]
    )


def _memory(tmp_path) -> ChromaLocalMemory:
    return ChromaLocalMemory(
        persist_directory=str(tmp_path),
        collection_name="smoke",
        embedding_function=_TestEmbedding(),
    )


def test_shared_text_memory_is_searchable_and_idempotent(tmp_path):
    memory = _memory(tmp_path)

    async def run():
        context = _context("u1", "team-a", memory)
        await memory.save_text_memory(
            "复购率只统计 CNY 有效订单",
            context,
            metadata={"scope": "shared", "source": "test"},
        )
        await memory.save_text_memory(
            "复购率只统计 CNY 有效订单",
            context,
            metadata={"scope": "shared", "source": "test"},
        )
        results = await memory.search_text_memories(
            "复购率", context, limit=5, similarity_threshold=0.0
        )
        return results

    results = asyncio.run(run())
    assert memory.collection.count() == 1
    assert any("CNY" in result.memory.content for result in results)

    # A second process/instance on the same directory must see the memory.
    restarted = _memory(tmp_path)
    results2 = asyncio.run(
        restarted.search_text_memories(
            "复购率",
            _context("u2", "team-b", restarted),
            limit=5,
            similarity_threshold=0.0,
        )
    )
    assert any("CNY" in result.memory.content for result in results2)


def test_user_and_team_isolation(tmp_path):
    memory = _memory(tmp_path)

    async def run():
        user_a = _context("u-a", "team-a", memory)
        user_b = _context("u-b", "team-b", memory)
        await memory.save_tool_usage(
            "用户 A 的私有问题",
            "run_sql",
            {"sql": "SELECT 1"},
            user_a,
            metadata={"scope": "user"},
        )
        await memory.save_tool_usage(
            "团队 A 的问题",
            "run_sql",
            {"sql": "SELECT 2"},
            user_a,
            metadata={"scope": "team"},
        )
        a_results = await memory.search_similar_usage(
            "用户 A 的私有问题", user_a, limit=5, similarity_threshold=0.0
        )
        b_results = await memory.search_similar_usage(
            "用户 A 的私有问题", user_b, limit=5, similarity_threshold=0.0
        )
        return a_results, b_results

    a_results, b_results = asyncio.run(run())
    assert any("A 的私有" in result.memory.question for result in a_results)
    assert all("A 的私有" not in result.memory.question for result in b_results)
    assert all("团队 A" not in result.memory.question for result in b_results)


def test_embedding_model_mismatch_fails_loudly(tmp_path):
    ChromaLocalMemory(
        persist_directory=str(tmp_path),
        collection_name="model_guard",
        embedding_function=_TestEmbedding(name="model-a"),
    )

    with pytest.raises(ValueError, match="Embedding model mismatch"):
        ChromaLocalMemory(
            persist_directory=str(tmp_path),
            collection_name="model_guard",
            embedding_function=_TestEmbedding(name="model-b"),
        )


def test_clear_removes_tool_and_text_memories(tmp_path):
    memory = _memory(tmp_path)

    async def run():
        context = _context("u-a", "team-a", memory)
        await memory.save_tool_usage(
            "问题",
            "run_sql",
            {"sql": "SELECT 1"},
            context,
            metadata={"scope": "user"},
        )
        await memory.save_text_memory("知识", context, metadata={"scope": "user"})
        cleared = await memory.clear_memories(context)
        return cleared

    assert asyncio.run(run()) == 2
    assert memory.collection.count() == 0


def test_private_memory_delete(tmp_path):
    memory = _memory(tmp_path)

    async def run():
        context = _context("u-a", "team-a", memory)
        await memory.save_tool_usage(
            "待删除的私有问题",
            "run_sql",
            {"sql": "SELECT 1"},
            context,
            metadata={"scope": "user"},
        )
        results = await memory.get_recent_memories(context, limit=5)
        assert results
        deleted = await memory.delete_by_id(context, results[0].memory_id or "")
        after = await memory.search_similar_usage(
            "待删除的私有问题", context, limit=5, similarity_threshold=0.0
        )
        return deleted, after

    deleted, after = asyncio.run(run())
    assert deleted is True
    assert after == []


def test_list_memories_returns_readable_documents_and_respects_scope(tmp_path):
    memory = _memory(tmp_path)

    async def run():
        owner = _context("u-a", "team-a", memory)
        other = _context("u-b", "team-b", memory)
        await memory.save_text_memory(
            "共享知识：净收入要扣退款",
            owner,
            metadata={"scope": "shared", "source": "test#shared", "version": 1},
        )
        await memory.save_tool_usage(
            "私有问题：各渠道 GMV",
            "run_sql",
            {
                "sql": "SELECT channel, SUM(amount_cents) FROM shop.orders GROUP BY channel"
            },
            owner,
            metadata={"scope": "user", "source": "test#private", "version": 1},
        )
        return await memory.list_memories(other), await memory.list_memories(owner)

    other_items, owner_items = asyncio.run(run())

    assert len(other_items) == 1
    assert other_items[0]["kind"] == "text"
    assert "净收入" in other_items[0]["content"]
    assert other_items[0]["source"] == "test#shared"

    assert len(owner_items) == 2
    tool_items = [item for item in owner_items if item["kind"] == "tool"]
    assert tool_items
    assert "GROUP BY channel" in tool_items[0]["sql"]
