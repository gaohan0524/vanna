"""
Base workflow handler interface.

Workflow triggers allow you to execute deterministic workflows in response to
user messages before they are sent to the LLM. This is useful for:
- Command handling (e.g., /help, /reset)
- Pattern-based routing (e.g., report generation)
- State-based workflows (e.g., onboarding flows)
- Quota enforcement with custom responses

本模块定义确定性工作流接口。它在用户消息进入 LLM 之前提供「短路」能力：命令处理、
规则路由、状态机工作流、配额拦截等确定性逻辑无需调用 LLM 即可完成，既省 token 又
保证响应可预期。
"""

from abc import ABC, abstractmethod
from typing import (
    TYPE_CHECKING,
    Optional,
    Union,
    List,
    AsyncGenerator,
    Callable,
    Awaitable,
)
from dataclasses import dataclass

if TYPE_CHECKING:
    from ..user.models import User
    from ..storage import Conversation
    from ...components import UiComponent
    from ..agent.agent import Agent


@dataclass
class WorkflowResult:
    """Result from a workflow handler attempt.

    When a workflow handles a message, it can optionally return UI components to stream
    to the user and/or mutate the conversation state.

    Attributes:
        should_skip_llm: If True, the workflow handled the message and LLM processing is skipped.
                         If False, the message continues to the agent/LLM.
        components: Optional UI components to stream back to the user.
                    Can be a list or async generator for streaming responses.
        conversation_mutation: Optional async callback to modify conversation state
                               (e.g., clearing messages, adding system events).

    Example:
        # Simple command response
        WorkflowResult(
            should_skip_llm=True,
            components=[RichTextComponent(content="Help text here")]
        )

        # With conversation mutation
        async def clear_history(conv):
            conv.messages.clear()

        WorkflowResult(
            should_skip_llm=True,
            components=[StatusCardComponent(...)],
            conversation_mutation=clear_history
        )

        # Not handled, continue to agent
        WorkflowResult(should_skip_llm=False)

    try_handle 的返回结果：should_skip_llm=True 表示本次消息已被确定性工作流处理，
    跳过 LLM；components 支持 list 或 AsyncGenerator 两种流式形态；conversation_mutation
    是可选回调，用于改写会话状态（如清空历史）。
    """

    should_skip_llm: bool
    components: Optional[
        Union[List["UiComponent"], AsyncGenerator["UiComponent", None]]
    ] = None
    conversation_mutation: Optional[Callable[["Conversation"], Awaitable[None]]] = None


class WorkflowHandler(ABC):
    """Base class for handling deterministic workflows before LLM processing.

    Implement this interface to intercept user messages and execute deterministic
    workflows instead of sending to the LLM. This is the first extensibility point
    in the agent's message processing pipeline, running after user resolution and
    conversation loading but before the message is added to conversation history
    or sent to the LLM.

    Use cases:
    - Slash commands (/help, /reset, /report)
    - Pattern-based routing (regex matching)
    - State-based workflows (onboarding, surveys)
    - Custom quota enforcement with helpful messages
    - Deterministic report generation
    - Starter UI (buttons, welcome messages) when conversation begins

    Example:
        class CommandWorkflow(WorkflowHandler):
            async def try_handle(self, agent, user, conversation, message):
                if message.startswith("/help"):
                    return WorkflowResult(
                        should_skip_llm=True,
                        components=[
                            RichTextComponent(
                                content="Available commands:\\n- /help\\n- /reset",
                                markdown=True
                            )
                        ]
                    )

                # Execute tool for reports
                if message.startswith("/report"):
                    tool = await agent.tool_registry.get_tool("generate_report")
                    result = await tool.execute(ToolContext(user=user), {})
                    return WorkflowResult(should_skip_llm=True, components=[result.ui_component])

                # Not handled, continue to agent
                return WorkflowResult(should_skip_llm=False)

            async def get_starter_ui(self, agent, user, conversation):
                return [
                    RichTextComponent(content=f"Welcome {user.username}!"),
                    ButtonComponent(label="Generate Report", value="/report"),
                ]

        agent = Agent(
            llm_service=...,
            tool_registry=...,
            user_resolver=...,
            workflow_handler=CommandWorkflow()
        )

    Observability:
        The agent automatically creates an "agent.workflow_handler" span when
        a WorkflowHandler is configured, allowing you to monitor handler
        performance and outcomes.

    确定性工作流处理器抽象基类。它是 Agent 消息处理链路中的第一个扩展点：在用户
    解析与会话加载之后、消息写入历史与调用 LLM 之前运行。try_handle 返回
    should_skip_llm=True 时短路 LLM（且消息不自动入历史）；get_starter_ui 用于
    会话打开时渲染欢迎/快捷操作。
    """

    @abstractmethod
    async def try_handle(
        self, agent: "Agent", user: "User", conversation: "Conversation", message: str
    ) -> WorkflowResult:
        """Attempt to handle a workflow for the given message.

        This method is called for every user message before it reaches the LLM.
        Inspect the message content, user context, and conversation state to
        decide whether to execute a deterministic workflow or allow normal
        agent processing.

        Args:
            agent: The agent instance, providing access to tool_registry, config,
                   and observability_provider for tool execution and logging.
            user: The user who sent the message, including their ID, permissions,
                  and metadata. Use this for permission checks or personalization.
            conversation: The current conversation context, including message history.
                          Can be inspected for state-based workflows.
            message: The user's raw message content.

        Returns:
            WorkflowResult with should_skip_llm=True to execute a workflow and skip LLM,
            or should_skip_llm=False to continue normal agent processing.

            When should_skip_llm=True:
            - The message is NOT added to conversation history automatically
            - The components are streamed to the user
            - The conversation_mutation callback (if provided) is executed
            - The agent returns without calling the LLM

            When should_skip_llm=False:
            - The message is added to conversation history
            - Normal agent processing continues (LLM call, tool execution, etc.)

        Example:
            async def try_handle(self, agent, user, conversation, message):
                # Pattern matching with tool execution
                if message.startswith("/report"):
                    # Execute tool from registry
                    tool = await agent.tool_registry.get_tool("generate_sales_report")
                    context = ToolContext(user=user, conversation=conversation)
                    result = await tool.execute(context, {})

                    return WorkflowResult(
                        should_skip_llm=True,
                        components=[...]
                    )

                # State-based workflow
                if user.metadata.get("needs_onboarding"):
                    return await self._onboarding_flow(agent, user, message)

                # Permission check
                if message.startswith("/admin") and "admin" not in user.permissions:
                    return WorkflowResult(
                        should_skip_llm=True,
                        components=[RichTextComponent(content="Access denied.")]
                    )

                # Continue to agent
                return WorkflowResult(should_skip_llm=False)

        对每条用户消息在进入 LLM 前调用，决定是否由确定性工作流接管（短路）还是放行。
        """
        pass

    async def get_starter_ui(
        self, agent: "Agent", user: "User", conversation: "Conversation"
    ) -> Optional[List["UiComponent"]]:
        """Provide UI components when a conversation starts.

        Override this method to show starter buttons, welcome messages,
        or quick actions when a new chat is opened by the user.

        This is called by the frontend/server when initializing a new
        conversation, before any user messages are sent.

        Args:
            agent: The agent instance, providing access to tool_registry, config,
                   and observability_provider for dynamic UI generation.
            user: The user starting the conversation
            conversation: The new conversation (typically empty)

        Returns:
            List of UI components to display, or None for no starter UI.
            Components can include buttons, welcome text, quick actions, etc.

        Example:
            async def get_starter_ui(self, agent, user, conversation):
                # Show role-based quick actions
                if "analyst" in user.permissions:
                    # Dynamically generate buttons based on available tools
                    report_tools = [
                        tool for tool in agent.tool_registry.list_tools()
                        if tool.startswith("report_")
                    ]

                    buttons = [
                        ButtonComponent(label=f"📊 {tool}", value=f"/{tool}")
                        for tool in report_tools
                    ]

                    return [
                        RichTextComponent(
                            content=f"Welcome back, {user.username}!",
                            markdown=True
                        ),
                        *buttons
                    ]

                # New user onboarding
                if user.metadata.get("is_new_user"):
                    return [
                        RichTextComponent(
                            content="# Welcome to Vanna!\\n\\nTry one of these to get started:",
                            markdown=True
                        ),
                        ButtonComponent(label="Show Example Query", value="/example"),
                        ButtonComponent(label="View Tutorial", value="/tutorial"),
                    ]

                return None

        会话开始（空消息 / starter_ui_request 标记）时提供欢迎组件；返回 None 表示
        不提供初始界面。
        """
        return None
