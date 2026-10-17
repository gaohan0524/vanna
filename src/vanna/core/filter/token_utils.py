"""
Token estimation utilities for context window management.

本模块提供上下文窗口管理所需的 token 估算能力。默认使用轻量的启发式估算（不引入
tiktoken 等重依赖），也支持注入精确的 tokenizer（如 tiktoken 的 encoding）以在需要
精确计数的场景使用。主流方案（OpenAI/LangChain 等）普遍按 token 预算做裁剪，因此
这里把「估算」与「窗口边界计算」抽象成共享工具，供滑动窗口 / 摘要压缩等内置过滤器
复用。
"""

import json
from typing import Any, Callable, List, Optional

# tokenizer 签名：接收字符串，返回 token 数量
TokenCounter = Callable[[str], int]


def estimate_tokens(
    text: Optional[str], tokenizer: Optional[TokenCounter] = None
) -> int:
    """Estimate the number of tokens in a string.

    Args:
        text: Text to estimate. Empty/None returns 0.
        tokenizer: Optional exact tokenizer (e.g. tiktoken encoding.encode). When
            provided it is used for exact counting; otherwise a heuristic is used.

    启发式：ASCII 约 4 字符/token，非 ASCII（CJK 等）约 1 字符/token，至少返回 1。
    """
    if not text:
        return 0
    if tokenizer is not None:
        return max(0, int(tokenizer(text)))

    ascii_chars = 0
    non_ascii_chars = 0
    for ch in text:
        if ord(ch) < 128:
            ascii_chars += 1
        else:
            non_ascii_chars += 1
    return max(1, (ascii_chars + 3) // 4 + non_ascii_chars)


def estimate_message_tokens(
    message: Any, tokenizer: Optional[TokenCounter] = None
) -> int:
    """Estimate tokens consumed by one message (content + tool calls + overhead).

    Args:
        message: A storage.Message-like object with ``content`` and optional
            ``tool_calls``.
        tokenizer: Optional exact tokenizer.
    """
    tokens = estimate_tokens(getattr(message, "content", None), tokenizer)
    for tool_call in getattr(message, "tool_calls", None) or []:
        tokens += estimate_tokens(getattr(tool_call, "name", ""), tokenizer)
        tokens += estimate_tokens(_tool_call_arguments_text(tool_call), tokenizer)
        # 粗略计入工具调用的协议/结构开销
        tokens += 8
    return tokens


def _tool_call_arguments_text(tool_call: Any) -> str:
    arguments = getattr(tool_call, "arguments", {}) or {}
    try:
        return json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(arguments)


def compute_window_start(
    messages: List[Any],
    max_tokens: int,
    tokenizer: Optional[TokenCounter] = None,
) -> int:
    """Return the start index of the most-recent window that fits ``max_tokens``.

    从最新一条向前累计 token，一旦超出预算即停止，返回应保留区间的起始下标。
    两点保证：
    1. 至少保留最后一条消息（即使单条就超预算，也不返回空窗口）。
    2. 窗口起点不会落在孤立的 ``tool`` 消息上——会向前补回到其所属的 assistant
       （带 tool_calls）消息，避免违反 OpenAI「tool 必须紧跟 assistant tool_calls」的协议。
    """
    total = 0
    start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        total += estimate_message_tokens(messages[i], tokenizer)
        if total > max_tokens and i < len(messages) - 1:
            start = i + 1
            break
        start = i

    # 协议完整性：窗口起点不能是 tool 消息
    while start < len(messages) and getattr(messages[start], "role", "") == "tool":
        start -= 1
    return max(start, 0)
