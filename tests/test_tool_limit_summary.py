"""
Tool iteration limit: final summary behavior.

验证 `max_tool_iterations` 触顶后会发起一次「不带 tools」的收尾总结，把已收集到的
结果转成用户可用的最佳回答；关闭该开关时保持原有警告文案。
"""

import pytest
from pydantic import BaseModel

from vanna import Agent, AgentConfig
from vanna.core.llm import (
    LlmRequest,
    LlmResponse,
    LlmService,
    LlmStreamChunk,
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


class _NoopTool(Tool[_Args]):
    @property
    def name(self) -> str:
        return "noop"

    @property
    def description(self) -> str:
        return "noop tool"

    def get_args_schema(self):
        return _Args

    async def execute(self, context: ToolContext, args: _Args) -> ToolResult:
        return ToolResult(success=True, result_for_llm="ok")


class _ToolLimitLlm(LlmService):
    """带 tools 时永远返回 tool_call；不带 tools 时返回总结文本。"""

    def __init__(self) -> None:
        self.calls: list[LlmRequest] = []

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        if request.tools:
            return LlmResponse(
                tool_calls=[
                    ToolCall(
                        id=f"t{len(self.calls)}", name="noop", arguments={}
                    )
                ],
                finish_reason="tool_calls",
            )
        return LlmResponse(content="SUMMARY_TEXT", finish_reason="stop")

    async def stream_request(self, request: LlmRequest):
        self.calls.append(request)
        if request.tools:
            yield LlmStreamChunk(
                tool_calls=[
                    ToolCall(
                        id=f"t{len(self.calls)}", name="noop", arguments={}
                    )
                ],
                finish_reason="tool_calls",
            )
        else:
            yield LlmStreamChunk(content="SUMMARY_TEXT", finish_reason="stop")

    async def validate_tools(self, tools):
        return []


def _make_agent(llm: LlmService, config: AgentConfig) -> Agent:
    registry = ToolRegistry()
    registry.register_local_tool(_NoopTool(), access_groups=[])
    return Agent(
        llm_service=llm,
        tool_registry=registry,
        user_resolver=_Resolver(),
        agent_memory=DemoAgentMemory(max_items=10),
        config=config,
    )


def _texts(components) -> list[str]:
    texts = []
    for component in components:
        simple = getattr(component, "simple_component", None)
        if simple is not None and getattr(simple, "text", None):
            texts.append(simple.text)

        rich = getattr(component, "rich_component", None)
        content = getattr(rich, "content", None) if rich is not None else None
        if isinstance(content, str) and content:
            texts.append(content)
    return texts


async def test_tool_limit_summarizes_when_enabled():
    llm = _ToolLimitLlm()
    agent = _make_agent(
        llm,
        AgentConfig(
            max_tool_iterations=1,
            stream_responses=False,
            summarize_on_tool_limit=True,
        ),
    )

    components = []
    async for component in agent.send_message(
        RequestContext(cookies={}, headers={}), "hi"
    ):
        components.append(component)

    # 触顶后有一次 tools=None 的收尾总结
    summary_calls = [request for request in llm.calls if request.tools is None]
    assert len(summary_calls) == 1
    assert "maximum number of tool calls" in summary_calls[0].messages[-1].content

    texts = _texts(components)
    assert any("SUMMARY_TEXT" in text for text in texts)
    assert any("tool-call limit" in text for text in texts)


async def test_tool_limit_without_summary_when_disabled():
    llm = _ToolLimitLlm()
    agent = _make_agent(
        llm,
        AgentConfig(
            max_tool_iterations=1,
            stream_responses=False,
            summarize_on_tool_limit=False,
        ),
    )

    components = []
    async for component in agent.send_message(
        RequestContext(cookies={}, headers={}), "hi"
    ):
        components.append(component)

    # 不应有 tools=None 的总结调用
    assert all(request.tools is not None for request in llm.calls)

    texts = _texts(components)
    assert any("Tool Execution Limit Reached" in text for text in texts)


async def test_tool_limit_summary_streaming():
    llm = _ToolLimitLlm()
    agent = _make_agent(
        llm,
        AgentConfig(
            max_tool_iterations=1,
            stream_responses=True,
            summarize_on_tool_limit=True,
        ),
    )

    components = []
    async for component in agent.send_message(
        RequestContext(cookies={}, headers={}), "hi"
    ):
        components.append(component)

    summary_calls = [request for request in llm.calls if request.tools is None]
    assert len(summary_calls) == 1

    texts = _texts(components)
    assert any("SUMMARY_TEXT" in text for text in texts)
