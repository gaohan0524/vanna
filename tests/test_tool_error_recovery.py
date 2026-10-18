"""
Tool error recovery tests.

验证 handle_tool_error 已接线，但只对「未捕获异常」触发的工具失败生效：
- 未捕获异常：按策略 RETRY / FALLBACK / SKIP / FAIL 处理；
- 业务错误（success=False 且无 exception_type）：不重试，继续回喂 LLM 自我修复。
"""

from typing import Optional

import pytest
from pydantic import BaseModel

from vanna import Agent, AgentConfig
from vanna.core.agent.agent import MAX_TOOL_RECOVERY_ATTEMPTS
from vanna.core.llm import LlmRequest, LlmResponse, LlmService, LlmStreamChunk
from vanna.core.recovery import (
    ErrorRecoveryStrategy,
    RecoveryAction,
    RecoveryActionType,
)
from vanna.core.registry import ToolRegistry
from vanna.core.tool import Tool, ToolCall, ToolContext, ToolResult
from vanna.core.user import RequestContext, User, UserResolver
from vanna.integrations.local.agent_memory import DemoAgentMemory


class _Resolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="u", group_memberships=["user"])


class _Args(BaseModel):
    pass


class _FlakyTool(Tool[_Args]):
    """前 ``fail_times`` 次调用抛异常，之后成功。"""

    def __init__(self, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.call_count = 0

    @property
    def name(self) -> str:
        return "flaky"

    @property
    def description(self) -> str:
        return "flaky tool"

    def get_args_schema(self):
        return _Args

    async def execute(self, context: ToolContext, args: _Args) -> ToolResult:
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise RuntimeError("transient failure")
        return ToolResult(success=True, result_for_llm="ok")


class _BusinessErrorTool(Tool[_Args]):
    """返回业务错误（不带 exception_type），不应触发重试。"""

    def __init__(self) -> None:
        self.call_count = 0

    @property
    def name(self) -> str:
        return "business"

    @property
    def description(self) -> str:
        return "business error tool"

    def get_args_schema(self):
        return _Args

    async def execute(self, context: ToolContext, args: _Args) -> ToolResult:
        self.call_count += 1
        return ToolResult(
            success=False,
            result_for_llm="invalid SQL",
            error="invalid SQL",
            metadata={"error_type": "sql_error"},
        )


class _TwoStepLlm(LlmService):
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.calls: list[LlmRequest] = []

    @staticmethod
    def _has_tool_result(request: LlmRequest) -> bool:
        return any(message.role == "tool" for message in request.messages)

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        if not self._has_tool_result(request):
            return LlmResponse(
                tool_calls=[
                    ToolCall(id="t1", name=self.tool_name, arguments={})
                ],
                finish_reason="tool_calls",
            )
        return LlmResponse(content="done", finish_reason="stop")

    async def stream_request(self, request: LlmRequest):
        self.calls.append(request)
        if not self._has_tool_result(request):
            yield LlmStreamChunk(
                tool_calls=[
                    ToolCall(id="t1", name=self.tool_name, arguments={})
                ],
                finish_reason="tool_calls",
            )
        else:
            yield LlmStreamChunk(content="done", finish_reason="stop")

    async def validate_tools(self, tools):
        return []


class _RetryOnceStrategy(ErrorRecoveryStrategy):
    async def handle_tool_error(self, error, context, attempt=1):
        if attempt == 1:
            return RecoveryAction(action=RecoveryActionType.RETRY, retry_delay_ms=0)
        return RecoveryAction(action=RecoveryActionType.FAIL, message=str(error))


class _AlwaysRetryStrategy(ErrorRecoveryStrategy):
    async def handle_tool_error(self, error, context, attempt=1):
        return RecoveryAction(action=RecoveryActionType.RETRY, retry_delay_ms=0)


class _FallbackStrategy(ErrorRecoveryStrategy):
    async def handle_tool_error(self, error, context, attempt=1):
        return RecoveryAction(
            action=RecoveryActionType.FALLBACK,
            fallback_value=ToolResult(success=True, result_for_llm="fallback result"),
        )


class _SkipStrategy(ErrorRecoveryStrategy):
    async def handle_tool_error(self, error, context, attempt=1):
        return RecoveryAction(
            action=RecoveryActionType.SKIP, message="tool skipped by strategy"
        )


def _make_agent(
    llm: LlmService,
    tool: Tool,
    strategy: Optional[ErrorRecoveryStrategy],
) -> Agent:
    registry = ToolRegistry()
    registry.register_local_tool(tool, access_groups=[])
    return Agent(
        llm_service=llm,
        tool_registry=registry,
        user_resolver=_Resolver(),
        agent_memory=DemoAgentMemory(max_items=10),
        config=AgentConfig(stream_responses=False, max_tool_iterations=3),
        error_recovery_strategy=strategy,
    )


async def _run(agent: Agent) -> None:
    async for _ in agent.send_message(
        RequestContext(cookies={}, headers={}), "hi"
    ):
        pass


async def test_unexpected_exception_is_retried():
    tool = _FlakyTool(fail_times=1)
    llm = _TwoStepLlm("flaky")
    agent = _make_agent(llm, tool, _RetryOnceStrategy())

    await _run(agent)

    assert tool.call_count == 2
    assert len(llm.calls) == 2
    # 第二次 LLM 请求里能看到成功结果
    tool_messages = [
        message
        for message in llm.calls[1].messages
        if message.role == "tool"
    ]
    assert tool_messages and "ok" in (tool_messages[-1].content or "")


async def test_no_strategy_keeps_original_result():
    tool = _FlakyTool(fail_times=1)
    llm = _TwoStepLlm("flaky")
    agent = _make_agent(llm, tool, None)

    await _run(agent)

    # 未配置策略：不重试，失败结果回喂 LLM
    assert tool.call_count == 1


async def test_business_error_is_not_retried():
    tool = _BusinessErrorTool()
    llm = _TwoStepLlm("business")
    agent = _make_agent(llm, tool, _AlwaysRetryStrategy())

    await _run(agent)

    # 业务错误（无 exception_type）不进入 handle_tool_error
    assert tool.call_count == 1


async def test_fallback_result_is_used():
    tool = _FlakyTool(fail_times=1)
    llm = _TwoStepLlm("flaky")
    agent = _make_agent(llm, tool, _FallbackStrategy())

    await _run(agent)

    assert tool.call_count == 1
    tool_messages = [
        message
        for message in llm.calls[1].messages
        if message.role == "tool"
    ]
    assert tool_messages and "fallback result" in (tool_messages[-1].content or "")


async def test_skip_returns_structured_failure():
    tool = _FlakyTool(fail_times=1)
    llm = _TwoStepLlm("flaky")
    agent = _make_agent(llm, tool, _SkipStrategy())

    await _run(agent)

    assert tool.call_count == 1
    tool_messages = [
        message
        for message in llm.calls[1].messages
        if message.role == "tool"
    ]
    assert tool_messages and "tool skipped by strategy" in (
        tool_messages[-1].content or ""
    )


async def test_retry_attempt_cap():
    tool = _FlakyTool(fail_times=100)
    llm = _TwoStepLlm("flaky")
    agent = _make_agent(llm, tool, _AlwaysRetryStrategy())

    await _run(agent)

    # 1 次初始调用 + MAX_TOOL_RECOVERY_ATTEMPTS 次重试
    assert tool.call_count == 1 + MAX_TOOL_RECOVERY_ATTEMPTS
