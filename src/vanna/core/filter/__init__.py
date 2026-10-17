"""
Conversation filtering system for managing conversation history.

本模块提供会话历史过滤器：既包含可扩展的抽象基类 ConversationFilter，也提供内置的
上下文窗口管理实现（滑动窗口 / 总结压缩），用于在长对话中控制 token 消耗。
"""

from .base import ConversationFilter
from .sliding_window import SlidingWindowFilter
from .summary import SummaryBufferFilter

__all__ = [
    "ConversationFilter",
    "SlidingWindowFilter",
    "SummaryBufferFilter",
]
