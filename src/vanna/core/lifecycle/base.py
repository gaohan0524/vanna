"""
Base lifecycle hook interface.

Lifecycle hooks allow you to intercept and customize agent behavior
at key points in the execution flow.

本模块定义生命周期钩子接口，在消息处理与工具执行的关键节点提供拦截点，用于配额
控制、日志、结果改写、校验等。这是 Agent 四个「消息/工具级」扩展点中唯一能主动
阻断流程的（before_tool 抛 AgentError 可阻止工具执行）。
"""

from abc import ABC
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..user.models import User
    from ..tool import Tool
    from ..tool.models import ToolContext, ToolResult


class LifecycleHook(ABC):
    """Hook into agent execution lifecycle.

    Subclass this to create custom hooks that can:
    - Modify messages before processing
    - Add logging or telemetry
    - Enforce quotas or rate limits
    - Transform tool results
    - Add custom validation

    Example:
        class LoggingHook(LifecycleHook):
            async def before_message(self, user: User, message: str) -> Optional[str]:
                print(f"User {user.username} sent: {message}")
                return None  # Don't modify

        agent = AgentRunner(
            llm_service=...,
            lifecycle_hooks=[LoggingHook(), QuotaCheckHook()]
        )

    生命周期钩子的抽象基类。四个方法均默认空实现/返回 None，遵循「返回 None 即不
    干预」的直通语义。Agent 分别在消息处理前后、工具执行前后依次调用所有钩子，
    前一个钩子的输出会作为后一个的输入（串联折叠）。
    """

    async def before_message(self, user: "User", message: str) -> Optional[str]:
        """Called before processing a user message.

        Args:
            user: User sending the message
            message: Original message content

        Returns:
            Modified message string, or None to keep original

        Raises:
            AgentError: To halt message processing (e.g., quota exceeded)

        在消息进入 workflow/LLM 之前调用。返回非 None 即用返回值替换消息；抛
        AgentError 可终止本次处理（如配额超限）。
        """
        return None

    async def after_message(self, result: Any) -> None:
        """Called after message has been fully processed.

        Args:
            result: Final result from message processing

        在整轮消息处理完成后调用，用于日志、计费、落库等收尾动作。
        """
        pass

    async def before_tool(self, tool: "Tool[Any]", context: "ToolContext") -> None:
        """Called before tool execution.

        Args:
            tool: Tool about to be executed
            context: Tool execution context

        Raises:
            AgentError: To prevent tool execution

        在工具执行前调用。抛 AgentError 可阻断工具执行（例如权限或配额校验）。
        """
        pass

    async def after_tool(self, result: "ToolResult") -> Optional["ToolResult"]:
        """Called after tool execution.

        Args:
            result: Result from tool execution

        Returns:
            Modified ToolResult, or None to keep original

        在工具执行后调用。返回新的 ToolResult 即替换结果（如脱敏、注入警告），
        返回 None 表示保留原结果。
        """
        return None
