"""
Sliding-window conversation filter.

本模块提供内置的「滑动窗口」上下文窗口管理过滤器：只保留最近若干 token 范围内的
历史消息，丢弃更早的消息。这是最主流、零 LLM 成本的上下文窗口管理手段（对应
LangChain 的 ConversationBufferWindowMemory、OpenAI 等「只保留最近 N 轮」策略）。
"""

from typing import List, Optional

from ..storage import Message
from .base import ConversationFilter
from .token_utils import TokenCounter, compute_window_start


class SlidingWindowFilter(ConversationFilter):
    """Keep only the most recent messages that fit within a token budget.

    Args:
        max_tokens: Token budget for the retained history (default 8000).
        tokenizer: Optional exact tokenizer; if None, a lightweight heuristic is used.
        keep_system_messages: If True, always keep system-role messages at the front.

    Example:
        agent = Agent(
            ...,
            conversation_filters=[SlidingWindowFilter(max_tokens=8000)],
        )
    """

    def __init__(
        self,
        max_tokens: int = 8000,
        tokenizer: Optional[TokenCounter] = None,
        keep_system_messages: bool = True,
    ) -> None:
        self.max_tokens = max_tokens
        self.tokenizer = tokenizer
        self.keep_system_messages = keep_system_messages

    async def filter_messages(self, messages: List[Message]) -> List[Message]:
        """Trim history to the most recent token window (order preserved)."""
        if self.keep_system_messages:
            system_msgs = [m for m in messages if m.role == "system"]
            others = [m for m in messages if m.role != "system"]
        else:
            system_msgs = []
            others = list(messages)

        if not others:
            return system_msgs

        start = compute_window_start(others, self.max_tokens, self.tokenizer)
        return system_msgs + others[start:]
