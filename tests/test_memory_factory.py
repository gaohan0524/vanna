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
