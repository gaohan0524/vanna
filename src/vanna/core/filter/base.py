"""
Base conversation filter interface.

Conversation filters allow you to transform conversation history before
it's sent to the LLM for processing.

本模块定义会话历史过滤器接口。它在「每次构建 LLM 请求之前」对会话消息做裁剪/脱敏/
摘要，用于控制上下文窗口、移除敏感信息、去重等，是防止 token 超限与信息泄露的
横切面扩展点。
"""

from abc import ABC
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..storage import Message


class ConversationFilter(ABC):
    """Filter for transforming conversation history.

    Subclass this to create custom filters that can:
    - Remove sensitive information
    - Summarize long conversations
    - Manage context window limits
    - Deduplicate similar messages
    - Prioritize recent or relevant messages

    Example:
        class ContextWindowFilter(ConversationFilter):
            def __init__(self, max_tokens: int = 8000):
                self.max_tokens = max_tokens

            async def filter_messages(self, messages: List[Message]) -> List[Message]:
                # Estimate tokens (rough approximation)
                total_tokens = 0
                filtered = []

                # Keep system message and recent messages
                for msg in reversed(messages):
                    msg_tokens = len(msg.content or "") // 4
                    if total_tokens + msg_tokens > self.max_tokens:
                        break
                    filtered.insert(0, msg)
                    total_tokens += msg_tokens

                return filtered

        agent = AgentRunner(
            llm_service=...,
            conversation_filters=[
                SensitiveDataFilter(),
                ContextWindowFilter(max_tokens=8000)
            ]
        )

    会话历史过滤器的抽象基类。默认实现原样返回消息（直通）。多个过滤器按顺序串联，
    前一个的输出是后一个的输入；过滤器只影响发往 LLM 的历史，不改动存储中的
    完整会话。
    """

    async def filter_messages(self, messages: List["Message"]) -> List["Message"]:
        """Filter and transform conversation messages.

        Args:
            messages: List of conversation messages

        Returns:
            Filtered/transformed list of messages

        Note:
            Filters are applied in order, so messages passed to later
            filters may already be modified by earlier filters.

        过滤/改写会话消息。默认原样返回；子类返回处理后的消息列表（可删减、摘要、
        去敏）。在 Text2SQL 长会话中常用于「只保留最近 N 条 + 系统提示」以控制
        token 消耗。
        """
        return messages
