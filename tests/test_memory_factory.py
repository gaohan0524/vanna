"""Memory factory smoke tests."""

from __future__ import annotations

import pytest
from vanna.integrations.local.agent_memory import DemoAgentMemory

from analytics_agent.agent_factory import build_memory
from analytics_agent.config import Settings


def test_default_memory_is_demo():
    memory = build_memory(Settings(memory_backend="demo"))

    assert isinstance(memory, DemoAgentMemory)


def test_unknown_memory_backend_fails_loudly():
    with pytest.raises(ValueError, match="Unsupported MEMORY_BACKEND"):
        build_memory(Settings(memory_backend="unknown"))


def test_chroma_startup_failure_falls_back_to_empty_memory(monkeypatch):
    import analytics_agent.memory.embedding as embedding_module
    import analytics_agent.memory.backends.chroma_local as chroma_module

    monkeypatch.setattr(
        embedding_module, "ChineseEmbeddingFunction", lambda *args, **kwargs: object()
    )

    def _broken(**kwargs):
        raise OSError("chroma unavailable")

    monkeypatch.setattr(chroma_module, "ChromaLocalMemory", _broken)

    memory = build_memory(Settings(memory_backend="chroma_local"))

    assert isinstance(memory, DemoAgentMemory)
