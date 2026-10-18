"""
Thinking indicator tests.

验证 include_thinking_indicators 开关现在真正生效：
- 开启时，首轮 LLM 调用前输出 "Thinking..." 状态栏更新；
- 关闭时不输出。
"""

from typing import List

from vanna import Agent, AgentConfig
from vanna.core.llm import LlmRequest, LlmResponse, LlmService, LlmStreamChunk
from vanna.core.registry import ToolRegistry
from vanna.core.user import RequestContext, User, UserResolver
from vanna.integrations.local.agent_memory import DemoAgentMemory


class _Resolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="u", group_memberships=["user"])


class _OneShotLlm(LlmService):
    async def send_request(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(content="ok", finish_reason="stop")

    async def stream_request(self, request: LlmRequest):
        yield LlmStreamChunk(content="ok", finish_reason="stop")

    async def validate_tools(self, tools):
        return []


def _agent(include_thinking: bool) -> Agent:
    return Agent(
        llm_service=_OneShotLlm(),
        tool_registry=ToolRegistry(),
        user_resolver=_Resolver(),
        agent_memory=DemoAgentMemory(max_items=10),
        config=AgentConfig(
            stream_responses=False,
            include_thinking_indicators=include_thinking,
        ),
    )


async def _status_messages(agent: Agent) -> List[str]:
    messages: List[str] = []
    async for component in agent.send_message(
        RequestContext(cookies={}, headers={}), "hi"
    ):
        rich = component.rich_component
        message = getattr(rich, "message", None) if rich is not None else None
        if isinstance(message, str):
            messages.append(message)
    return messages


async def test_thinking_indicator_enabled():
    messages = await _status_messages(_agent(True))
    assert "Thinking..." in messages


async def test_thinking_indicator_disabled():
    messages = await _status_messages(_agent(False))
    assert "Thinking..." not in messages
