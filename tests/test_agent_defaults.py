"""
Regression tests: Agent must not share mutable defaults across instances.

历史问题：`Agent.__init__` 曾使用 `config: AgentConfig = AgentConfig()`、
`lifecycle_hooks: List[...] = []` 等可变默认参数。由于 Python 的默认参数只在
函数定义时求值一次，多个未显式传参的 Agent 会共享同一份 config / list，导致
跨实例状态串扰。
"""

from vanna import Agent
from vanna.core import AgentConfig
from vanna.core.registry import ToolRegistry
from vanna.core.user import RequestContext, User, UserResolver
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.mock import MockLlmService


class _Resolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        return User(id="u")


def _make_agent(**kwargs) -> Agent:
    return Agent(
        llm_service=MockLlmService(),
        tool_registry=ToolRegistry(),
        user_resolver=_Resolver(),
        agent_memory=DemoAgentMemory(max_items=10),
        **kwargs,
    )


def test_default_config_is_not_shared():
    a, b = _make_agent(), _make_agent()
    assert a.config is not b.config

    a.config.temperature = 0.1
    assert a.config.temperature == 0.1
    assert b.config.temperature == 0.7  # 默认值不受影响


def test_default_lists_are_not_shared():
    a, b = _make_agent(), _make_agent()

    a.lifecycle_hooks.append(object())
    a.llm_middlewares.append(object())
    a.context_enrichers.append(object())
    a.conversation_filters.append(object())

    assert b.lifecycle_hooks == []
    assert b.llm_middlewares == []
    assert b.context_enrichers == []
    assert b.conversation_filters == []


def test_default_system_prompt_builder_is_not_shared():
    a, b = _make_agent(), _make_agent()
    assert a.system_prompt_builder is not b.system_prompt_builder


def test_explicit_config_is_preserved():
    cfg = AgentConfig(temperature=0.2)
    agent = _make_agent(config=cfg)
    assert agent.config is cfg


def test_explicit_lists_are_copied():
    hooks: list = []
    middlewares: list = []
    enrichers: list = []
    filters: list = []

    agent = _make_agent(
        lifecycle_hooks=hooks,
        llm_middlewares=middlewares,
        context_enrichers=enrichers,
        conversation_filters=filters,
    )

    # 传入的 list 被防御性拷贝，Agent 不会被外部后续修改影响
    assert agent.lifecycle_hooks is not hooks
    hooks.append(object())
    assert agent.lifecycle_hooks == []
