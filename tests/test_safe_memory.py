"""Read-path degradation tests for SafeAgentMemory."""

from __future__ import annotations

import asyncio

from analytics_agent.memory.safe import SafeAgentMemory


class _BrokenMemory:
    async def search_text_memories(self, *args, **kwargs):
        raise RuntimeError("backend down")

    async def search_similar_usage(self, *args, **kwargs):
        raise RuntimeError("backend down")

    async def get_recent_memories(self, *args, **kwargs):
        raise RuntimeError("backend down")

    async def get_recent_text_memories(self, *args, **kwargs):
        raise RuntimeError("backend down")


def test_read_failures_return_empty():
    memory = SafeAgentMemory(_BrokenMemory())  # type: ignore[arg-type]

    async def run():
        return (
            await memory.search_text_memories("q", None),  # type: ignore[arg-type]
            await memory.search_similar_usage("q", None),  # type: ignore[arg-type]
            await memory.get_recent_memories(None),  # type: ignore[arg-type]
            await memory.get_recent_text_memories(None),  # type: ignore[arg-type]
        )

    assert asyncio.run(run()) == ([], [], [], [])
