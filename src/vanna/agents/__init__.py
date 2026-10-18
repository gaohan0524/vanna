"""
Agent factory helpers for quickstart / demo servers.

本模块提供「开箱即用」的智能体工厂，供 CLI 默认分支（`vanna` 不带 --example）与
快速演示使用。生产环境请自行构造 Agent 并注入真实认证/存储后端。
"""

from __future__ import annotations

from typing import Optional

from ..core import Agent, LlmService, ToolRegistry
from ..core.user import User
from ..core.user.request_context import RequestContext
from ..core.user.resolver import UserResolver
from ..integrations.local.agent_memory import DemoAgentMemory


class _DemoUserResolver(UserResolver):
    """极简身份解析：从 cookie 的 ``vanna_email`` 读取身份，缺省返回 guest。

    仅供演示/quickstart 使用；生产请实现自己的 UserResolver 对接真实认证。
    """

    async def resolve_user(self, request_context: RequestContext) -> User:
        email = (request_context.cookies or {}).get(
            "vanna_email", "guest@example.com"
        )
        return User(id=email, email=email, group_memberships=["user"])


def create_basic_agent(
    llm_service: LlmService,
    *,
    tool_registry: Optional[ToolRegistry] = None,
    user_resolver: Optional[UserResolver] = None,
    agent_memory=None,
) -> Agent:
    """Create a minimal, self-contained Agent (quickstart / CLI default).

    Args:
        llm_service: LLM 服务实现（CLI 默认传入 MockLlmService）。
        tool_registry: 可选；缺省为空 ToolRegistry。
        user_resolver: 可选；缺省为基于 cookie 的极简解析器。
        agent_memory: 可选；缺省为 DemoAgentMemory（内存态）。
    """
    return Agent(
        llm_service=llm_service,
        tool_registry=tool_registry or ToolRegistry(),
        user_resolver=user_resolver or _DemoUserResolver(),
        agent_memory=agent_memory or DemoAgentMemory(),
    )


__all__ = ["create_basic_agent"]
