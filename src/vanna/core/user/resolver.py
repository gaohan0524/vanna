"""
User resolver interface for web request authentication.

This module provides the abstract base class for resolving web requests
to authenticated User objects.

本模块定义身份解析的插件化接口 UserResolver。它是 Vanna 接入真实业务认证系统的唯一
扩展点：任何登录体系（JWT、Session、LDAP、SSO 等）只需实现 resolve_user，即可让框架
获得「当前用户是谁、属于哪些组」的能力，进而驱动工具与 UI 的权限控制。
"""

from abc import ABC, abstractmethod

from .models import User
from .request_context import RequestContext


class UserResolver(ABC):
    """Resolves web requests to authenticated users.

    Implementations of this interface handle the specifics of extracting
    user identity from request context (cookies, headers, tokens, etc.)
    and creating authenticated User objects.

    Example:
        class JwtUserResolver(UserResolver):
            async def resolve_user(self, request_context: RequestContext) -> User:
                token = request_context.get_header('Authorization')
                # ... validate JWT and extract user info
                return User(id=user_id, username=username, email=email)

    身份解析的抽象基类。Agent 在每次消息处理的最前面调用 resolve_user，得到的 User
    会被注入 ToolContext 并参与权限判定，因此它是整条链路的信任锚点。
    """

    @abstractmethod
    async def resolve_user(self, request_context: RequestContext) -> User:
        """Resolve user from request context.

        Args:
            request_context: Structured request context with cookies, headers, etc.

        Returns:
            Authenticated User object

        Raises:
            Can raise exceptions for authentication failures

        从请求上下文解析出已认证用户。实现时请确保 group_memberships 正确赋值——
        它直接决定用户能看到哪些工具、哪些 UI 信息（如 SQL）。认证失败可抛异常，
        由 Agent.send_message 的容错壳兜底为错误卡片。
        """
        pass
