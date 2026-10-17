"""
Cancellation tests: token-level interrupt must abort the in-flight LLM stream.

验证协作式取消：cancel_event 置位后，Agent 应抛出 asyncio.CancelledError，并中止
在途的 LLM 流式调用（不再产出剩余 token），且不污染正常（未取消）路径。
"""

import asyncio

import pytest
from pydantic import BaseModel

from vanna import Agent
from vanna.core.llm import LlmRequest, LlmResponse, LlmStreamChunk, LlmService
from vanna.core.registry import ToolRegistry
from vanna.core.tool import Tool, ToolContext, ToolResult
from vanna.core.user import User
from vanna.core.user.request_context import RequestContext
from vanna.core.user.resolver import UserResolver
from vanna.core.rich_component import ComponentType
from vanna.integrations.local.agent_memory import DemoAgentMemory


class _TestResolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="u", email="u@example.com", group_memberships=["admin", "user"])


class _SlowStreamLlm(LlmService):
    """逐个 token 缓慢流式产出，用于观察取消能否中止在途的流。"""

    def __init__(self, n_chunks: int = 100, delay: float = 0.05):
        self.n_chunks = n_chunks
        self.delay = delay
        self.yielded = 0

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(content="done", finish_reason="stop")

    async def stream_request(self, request: LlmRequest):
        for i in range(self.n_chunks):
            await asyncio.sleep(self.delay)
            self.yielded += 1
            yield LlmStreamChunk(
                content=f"tok{i} ",
                finish_reason="stop" if i == self.n_chunks - 1 else None,
            )

    async def validate_tools(self, tools):
        return []


class _EchoArgs(BaseModel):
    text: str = ""


class _EchoTool(Tool[_EchoArgs]):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo text."

    def get_args_schema(self) -> type:
        return _EchoArgs

    async def execute(self, context: ToolContext, args: _EchoArgs) -> ToolResult:
        return ToolResult(success=True, result_for_llm=f"echo: {args.text}")


def _make_agent(llm: LlmService) -> Agent:
    registry = ToolRegistry()
    registry.register_local_tool(_EchoTool(), access_groups=[])
    return Agent(
        llm_service=llm,
        tool_registry=registry,
        user_resolver=_TestResolver(),
        agent_memory=DemoAgentMemory(max_items=100),
    )


async def test_stream_cancellation_aborts_inflight_llm_call():
    """cancel_event 置位后，send_message 抛 CancelledError 且不再产出剩余 token。"""
    llm = _SlowStreamLlm(n_chunks=100, delay=0.05)
    agent = _make_agent(llm)
    cancel_event = asyncio.Event()

    text_seen = 0
    with pytest.raises(asyncio.CancelledError):
        async for comp in agent.send_message(
            RequestContext(cookies={}, headers={}), "hi", cancel_event=cancel_event
        ):
            if comp.rich_component.type == ComponentType.TEXT:
                text_seen += 1
                if text_seen == 1:
                    cancel_event.set()

    # 流被提前中止：100 个 token 不应全部产出
    assert text_seen >= 1
    assert llm.yielded < llm.n_chunks


async def test_send_message_without_cancel_event_is_unaffected():
    """未传 cancel_event 时行为不变：完整跑完流式回答。"""
    llm = _SlowStreamLlm(n_chunks=3, delay=0.01)
    agent = _make_agent(llm)

    components = []
    async for comp in agent.send_message(RequestContext(cookies={}, headers={}), "hi"):
        components.append(comp)

    text = [
        c.rich_component
        for c in components
        if c.rich_component.type == ComponentType.TEXT
    ]
    assert text, "should stream token-level text components"
    assert text[-1].content == "tok0 tok1 tok2 "
    assert llm.yielded == 3
