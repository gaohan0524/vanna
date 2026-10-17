"""
Tests for LLM error recovery wiring and streaming aggregation.

覆盖 error_recovery_strategy 接线（_invoke_llm_with_recovery）与
_handle_streaming_response 对 finish_reason / usage 的聚合。
"""

import pytest
from unittest.mock import MagicMock

from vanna import Agent
from vanna.core.llm import LlmRequest, LlmResponse, LlmStreamChunk
from vanna.core.recovery import (
    ErrorRecoveryStrategy,
    RecoveryAction,
    RecoveryActionType,
)
from vanna.core.rich_component import ComponentLifecycle
from vanna.core.registry import ToolRegistry
from vanna.core.user import User
from vanna.core.user.request_context import RequestContext
from vanna.core.user.resolver import UserResolver
from vanna.integrations.local.agent_memory import DemoAgentMemory


class _TestResolver(UserResolver):
    """测试用：始终返回同一个用户。"""

    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="test", email="test@example.com", group_memberships=[])


def make_agent(strategy: ErrorRecoveryStrategy | None = None) -> Agent:
    """构造一个最小可用、带指定恢复策略的 Agent。"""
    return Agent(
        llm_service=MagicMock(),
        tool_registry=ToolRegistry(),
        user_resolver=_TestResolver(),
        agent_memory=DemoAgentMemory(max_items=100),
        error_recovery_strategy=strategy,
    )


def make_request() -> LlmRequest:
    return LlmRequest(
        messages=[],
        user=User(id="test", email="test@example.com", group_memberships=[]),
    )


class _RetryThenFailStrategy(ErrorRecoveryStrategy):
    """前 N 次返回 RETRY，之后返回 FAIL；记录调用次数。"""

    def __init__(self, retries: int = 1):
        self.retries = retries
        self.calls = 0

    async def handle_llm_error(self, error, request, attempt):
        self.calls += 1
        if attempt <= self.retries:
            return RecoveryAction(action=RecoveryActionType.RETRY, retry_delay_ms=1)
        return RecoveryAction(action=RecoveryActionType.FAIL)


class _FailStrategy(ErrorRecoveryStrategy):
    async def handle_llm_error(self, error, request, attempt):
        return RecoveryAction(action=RecoveryActionType.FAIL)


class _FallbackStrategy(ErrorRecoveryStrategy):
    def __init__(self, fallback: LlmResponse):
        self.fallback = fallback

    async def handle_llm_error(self, error, request, attempt):
        return RecoveryAction(
            action=RecoveryActionType.FALLBACK, fallback_value=self.fallback
        )


class _SkipStrategy(ErrorRecoveryStrategy):
    async def handle_llm_error(self, error, request, attempt):
        return RecoveryAction(action=RecoveryActionType.SKIP)


class _AlwaysRetryStrategy(ErrorRecoveryStrategy):
    async def handle_llm_error(self, error, request, attempt):
        return RecoveryAction(action=RecoveryActionType.RETRY)


class TestInvokeLlmWithRecovery:
    """测试 _invoke_llm_with_recovery 的错误恢复循环。"""

    async def test_no_strategy_direct_call(self):
        agent = make_agent(strategy=None)
        calls = {"n": 0}

        async def invoke() -> LlmResponse:
            calls["n"] += 1
            return LlmResponse(content="ok")

        result = await agent._invoke_llm_with_recovery(make_request(), invoke)
        assert result.content == "ok"
        assert calls["n"] == 1

    async def test_retry_then_success(self):
        strategy = _RetryThenFailStrategy(retries=1)
        agent = make_agent(strategy=strategy)
        calls = {"n": 0}

        async def invoke() -> LlmResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return LlmResponse(content="ok")

        result = await agent._invoke_llm_with_recovery(make_request(), invoke)
        assert result.content == "ok"
        assert calls["n"] == 2
        assert strategy.calls == 1

    async def test_fail_propagates(self):
        agent = make_agent(strategy=_FailStrategy())

        async def invoke() -> LlmResponse:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await agent._invoke_llm_with_recovery(make_request(), invoke)

    async def test_fallback_returns_value(self):
        fallback = LlmResponse(content="fallback")
        agent = make_agent(strategy=_FallbackStrategy(fallback))

        async def invoke() -> LlmResponse:
            raise RuntimeError("boom")

        result = await agent._invoke_llm_with_recovery(make_request(), invoke)
        assert result.content == "fallback"

    async def test_skip_returns_empty(self):
        agent = make_agent(strategy=_SkipStrategy())

        async def invoke() -> LlmResponse:
            raise RuntimeError("boom")

        result = await agent._invoke_llm_with_recovery(make_request(), invoke)
        assert result.content is None
        assert result.tool_calls is None

    async def test_retry_limit_exceeded(self):
        """永远 RETRY 的策略应在防御上限后抛出，避免死循环。"""
        agent = make_agent(strategy=_AlwaysRetryStrategy())

        async def invoke() -> LlmResponse:
            raise RuntimeError("always fails")

        with pytest.raises(RuntimeError, match="always fails"):
            await agent._invoke_llm_with_recovery(make_request(), invoke)


class TestStreamingAggregation:
    """测试 _handle_streaming_response 聚合 finish_reason 与 usage。"""

    async def test_collects_finish_reason_and_usage(self):
        agent = make_agent()

        async def fake_stream(request):
            yield LlmStreamChunk(content="Hello")
            yield LlmStreamChunk(content=" world")
            yield LlmStreamChunk(
                finish_reason="stop",
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            )

        agent.llm_service.stream_request = fake_stream

        request = make_request()
        response = await agent._handle_streaming_response(request)

        assert response.content == "Hello world"
        assert response.finish_reason == "stop"
        assert response.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }


class TestTokenStreaming:
    """测试 _stream_response_components 的 token 级打字机输出。"""

    async def test_streams_token_level_components(self):
        agent = make_agent()

        async def fake_stream(request):
            yield LlmStreamChunk(content="Hello")
            yield LlmStreamChunk(content=" ")
            yield LlmStreamChunk(content="world")
            yield LlmStreamChunk(finish_reason="stop")

        agent.llm_service.stream_request = fake_stream

        holder = {}
        components = []
        async for comp in agent._stream_response_components(make_request(), holder):
            components.append(comp)

        # 3 个文本片段 → 1 个 create + 2 个 update
        assert len(components) == 3
        assert components[0].rich_component.lifecycle == ComponentLifecycle.CREATE
        assert components[1].rich_component.lifecycle == ComponentLifecycle.UPDATE
        assert components[2].rich_component.lifecycle == ComponentLifecycle.UPDATE

        # 同一 id（前端据此增量更新同一节点）
        first_id = components[0].rich_component.id
        assert components[1].rich_component.id == first_id
        assert components[2].rich_component.id == first_id

        # 文本逐步累积
        assert components[0].rich_component.content == "Hello"
        assert components[2].rich_component.content == "Hello world"

        # 聚合结果正确写入 holder
        assert holder["response"].content == "Hello world"

    async def test_no_content_yields_no_components(self):
        """纯工具调用（无文本）时不应产出任何文本组件。"""
        agent = make_agent()

        async def fake_stream(request):
            yield LlmStreamChunk(finish_reason="tool_calls")

        agent.llm_service.stream_request = fake_stream

        holder = {}
        components = []
        async for comp in agent._stream_response_components(make_request(), holder):
            components.append(comp)

        assert components == []
        assert holder["response"].content is None
