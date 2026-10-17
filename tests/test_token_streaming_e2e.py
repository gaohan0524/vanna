"""
End-to-end test: token-level streaming must not break the tool loop.

用脚本化的 LlmService 模拟 ollama 式流式响应（说明文字 -> tool_calls -> 最终答案），
验证 token 级流式输出与工具循环协同正确。
"""

import asyncio
import time

import pytest
from pydantic import BaseModel

from vanna import Agent
from vanna.core.llm import LlmRequest, LlmResponse, LlmStreamChunk, LlmService
from vanna.core.registry import ToolRegistry
from vanna.core.tool import Tool, ToolContext, ToolResult, ToolCall
from vanna.core.user import User
from vanna.core.user.request_context import RequestContext
from vanna.core.user.resolver import UserResolver
from vanna.core.rich_component import ComponentType
from vanna.integrations.local.agent_memory import DemoAgentMemory


class _TestResolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="u", email="u@example.com", group_memberships=["admin", "user"])


class _EchoArgs(BaseModel):
    text: str = ""


class _EchoTool(Tool[_EchoArgs]):
    def __init__(self) -> None:
        self.executed_with = []

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the given text back."

    def get_args_schema(self) -> type:
        return _EchoArgs

    async def execute(self, context: ToolContext, args: _EchoArgs) -> ToolResult:
        self.executed_with.append(args.text)
        return ToolResult(success=True, result_for_llm=f"echo: {args.text}")


class _ScriptedLlm(LlmService):
    """按脚本逐轮流式返回 chunk；send_request 走相同脚本的非流式聚合。"""

    def __init__(self, script):
        self.script = [list(chunks) for chunks in script]
        self.calls = 0

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        chunks = self.script[self.calls]
        self.calls += 1
        content = "".join(c.content or "" for c in chunks)
        calls = [tc for c in chunks for tc in (c.tool_calls or [])]
        return LlmResponse(content=content or None, tool_calls=calls or None)

    async def stream_request(self, request: LlmRequest):
        chunks = self.script[self.calls]
        self.calls += 1
        for c in chunks:
            yield c

    async def validate_tools(self, tools):
        return []


def _make_agent(llm):
    registry = ToolRegistry()
    echo = _EchoTool()
    registry.register_local_tool(echo, access_groups=[])
    agent = Agent(
        llm_service=llm,
        tool_registry=registry,
        user_resolver=_TestResolver(),
        agent_memory=DemoAgentMemory(max_items=100),
    )
    return agent, echo


async def test_streaming_tool_loop_completes():
    """流式路径：说明文字 + tool_call -> 工具执行 -> 最终答案，循环完整。"""
    script = [
        [
            LlmStreamChunk(content="我先调用工具查询。"),
            LlmStreamChunk(
                tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
                finish_reason="tool_calls",
            ),
        ],
        [
            LlmStreamChunk(content="最终"),
            LlmStreamChunk(content="答案是 42。"),
            LlmStreamChunk(finish_reason="stop"),
        ],
    ]
    llm = _ScriptedLlm(script)
    agent, echo = _make_agent(llm)

    components = []
    async for comp in agent.send_message(RequestContext(cookies={}, headers={}), "test"):
        components.append(comp)

    # 工具被实际执行
    assert echo.executed_with == ["hi"]

    # LLM 被调用两轮（工具轮 + 最终答案轮）
    assert llm.calls == 2

    # 存在 token 级文本组件（TEXT 类型），且最后一轮答案文本完整
    text_components = [
        c.rich_component
        for c in components
        if c.rich_component.type == ComponentType.TEXT
    ]
    assert text_components, "should stream token-level text components"
    assert text_components[-1].content == "最终答案是 42。"


async def test_non_streaming_tool_loop_still_works():
    """非流式路径回归：同样的脚本，工具循环仍完整。"""
    script = [
        [
            LlmStreamChunk(content="我先调用工具查询。"),
            LlmStreamChunk(
                tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
                finish_reason="tool_calls",
            ),
        ],
        [
            LlmStreamChunk(content="答案是 42。"),
            LlmStreamChunk(finish_reason="stop"),
        ],
    ]
    llm = _ScriptedLlm(script)
    agent, echo = _make_agent(llm)
    # 关闭流式
    agent.config = agent.config.model_copy(update={"stream_responses": False})

    components = []
    async for comp in agent.send_message(RequestContext(cookies={}, headers={}), "test"):
        components.append(comp)

    assert echo.executed_with == ["hi"]
    assert llm.calls == 2
    text_components = [
        c.rich_component
        for c in components
        if c.rich_component.type == ComponentType.TEXT
    ]
    assert text_components
    assert text_components[-1].content == "答案是 42。"


class _SlowTool(Tool):
    """记录执行时间戳的慢工具，用于验证多工具并行执行。"""

    def __init__(self, name: str, delay: float = 0.1):
        self._name = name
        self.delay = delay
        self.start = None
        self.end = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "slow tool for parallelism test"

    def get_args_schema(self) -> type:
        class _Args(BaseModel):
            pass

        return _Args

    async def execute(self, context, args) -> ToolResult:
        self.start = time.monotonic()
        await asyncio.sleep(self.delay)
        self.end = time.monotonic()
        return ToolResult(success=True, result_for_llm=f"done {self._name}")


async def test_multiple_tools_execute_in_parallel():
    """LLM 一次返回多个 tool_calls 时应并行执行，而非串行逐个 await。"""
    tool_a = _SlowTool("slow_a")
    tool_b = _SlowTool("slow_b")
    registry = ToolRegistry()
    registry.register_local_tool(tool_a, access_groups=[])
    registry.register_local_tool(tool_b, access_groups=[])

    script = [
        [
            LlmStreamChunk(
                tool_calls=[
                    ToolCall(id="t1", name="slow_a", arguments={}),
                    ToolCall(id="t2", name="slow_b", arguments={}),
                ],
                finish_reason="tool_calls",
            ),
        ],
        [LlmStreamChunk(content="done", finish_reason="stop")],
    ]
    llm = _ScriptedLlm(script)
    agent = Agent(
        llm_service=llm,
        tool_registry=registry,
        user_resolver=_TestResolver(),
        agent_memory=DemoAgentMemory(max_items=100),
    )

    async for _ in agent.send_message(RequestContext(cookies={}, headers={}), "go"):
        pass

    # 两个工具的执行时间窗口有重叠 => 并行执行（串行时 overlap 为负）
    overlap = min(tool_a.end, tool_b.end) - max(tool_a.start, tool_b.start)
    assert overlap > 0, "tools should execute in parallel (overlapping time windows)"
