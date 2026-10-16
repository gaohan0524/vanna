"""
Base context enricher interface.

Context enrichers allow you to add additional data to the ToolContext
before tools are executed.

本模块定义上下文增强器接口，用于在工具执行前向 ToolContext（主要是其 metadata）
注入额外数据，如用户偏好、会话状态、时区等，供工具按需读取。
"""

from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tool.models import ToolContext


class ToolContextEnricher(ABC):
    """Enricher for adding data to ToolContext.

    Subclass this to create custom enrichers that can:
    - Add user preferences from database
    - Inject session state
    - Add temporal context (timezone, current date)
    - Include user history or profile data
    - Add environment-specific configuration

    Example:
        class UserPreferencesEnricher(ToolContextEnricher):
            def __init__(self, db):
                self.db = db

            async def enrich_context(self, context: ToolContext) -> ToolContext:
                # Fetch user preferences
                prefs = await self.db.get_user_preferences(context.user.id)

                # Add to context metadata
                context.metadata["preferences"] = prefs
                context.metadata["timezone"] = prefs.get("timezone", "UTC")

                return context

        agent = AgentRunner(
            llm_service=...,
            context_enrichers=[UserPreferencesEnricher(db), SessionEnricher()]
        )

    上下文增强器的抽象基类。默认原样返回 context（直通）。增强器在 Agent 构造
    ToolContext 之后、工具执行之前运行，通常往 context.metadata 写入工具需要
    的附加信息（如用户时区、默认数据库等）。
    """

    async def enrich_context(self, context: "ToolContext") -> "ToolContext":
        """Enrich the tool execution context with additional data.

        Args:
            context: The tool context to enrich

        Returns:
            Enriched context (typically modified in-place)

        Note:
            Enrichers typically modify the context.metadata dict to add
            additional data that tools can access.

        向工具上下文注入附加数据。默认原样返回；子类一般就地修改 context.metadata，
        使后续工具能通过 context.metadata 读取这些数据。
        """
        return context
