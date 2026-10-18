"""
Core exception hierarchy tests.

验证 `core/errors.py` 的语义：
- 所有自定义异常继承 AgentError；
- LLM 恢复失败（FAIL / 重试上限）抛出 LlmServiceError，并保留原始异常为 __cause__。
"""

from unittest.mock import MagicMock

import pytest

from vanna import Agent, AgentConfig
from vanna.core.errors import (
    AgentError,
    ConversationNotFoundError,
    LlmServiceError,
    PermissionError,
    ToolExecutionError,
    ToolNotFoundError,
    ValidationError,
)
from vanna.core.llm import LlmRequest
from vanna.core.recovery import (
    ErrorRecoveryStrategy,
    RecoveryAction,
    RecoveryActionType,
)
from vanna.core.registry import ToolRegistry
from vanna.core.user import RequestContext, User, UserResolver
from vanna.integrations.local.agent_memory import DemoAgentMemory


class _Resolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="u", group_memberships=["user"])


class _FailStrategy(ErrorRecoveryStrategy):
    async def handle_llm_error(self, error, request, attempt=1):
        return RecoveryAction(action=RecoveryActionType.FAIL)


def _agent(strategy=None) -> Agent:
    return Agent(
        llm_service=MagicMock(),
        tool_registry=ToolRegistry(),
        user_resolver=_Resolver(),
        agent_memory=DemoAgentMemory(max_items=10),
        config=AgentConfig(stream_responses=False),
        error_recovery_strategy=strategy,
    )


def _request() -> LlmRequest:
    return LlmRequest(messages=[], user=User(id="u"))


def test_exception_hierarchy():
    for exc_type in (
        ToolExecutionError,
        ToolNotFoundError,
        PermissionError,
        ConversationNotFoundError,
        LlmServiceError,
        ValidationError,
    ):
        assert issubclass(exc_type, AgentError)
        assert issubclass(exc_type, Exception)


async def test_llm_service_error_on_fail_preserves_cause():
    agent = _agent(_FailStrategy())

    async def invoke():
        raise RuntimeError("boom")

    with pytest.raises(LlmServiceError) as exc_info:
        await agent._invoke_llm_with_recovery(_request(), invoke)

    assert "boom" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    # AgentError 可作为统一兜底
    with pytest.raises(AgentError):
        await agent._invoke_llm_with_recovery(_request(), invoke)


async def test_llm_service_error_on_retry_limit():
    class _AlwaysRetry(ErrorRecoveryStrategy):
        async def handle_llm_error(self, error, request, attempt=1):
            return RecoveryAction(action=RecoveryActionType.RETRY, retry_delay_ms=0)

    agent = _agent(_AlwaysRetry())

    async def invoke():
        raise RuntimeError("always fails")

    with pytest.raises(LlmServiceError, match="always fails") as exc_info:
        await agent._invoke_llm_with_recovery(_request(), invoke)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
