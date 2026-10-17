"""
Context window management tests: built-in sliding window & summary compression.

验证内置的上下文窗口管理过滤器：滑动窗口正确裁剪最旧消息、保留 system 消息、维护
工具调用协议完整性；总结压缩过滤器正确把旧消息折叠成摘要并增量更新。
"""

from typing import List

import pytest

from vanna.core import (
    LlmRequest,
    LlmResponse,
    LlmService,
    LlmStreamChunk,
    Message,
    SlidingWindowFilter,
    SummaryBufferFilter,
    ToolCall,
)
from vanna.core.filter.token_utils import compute_window_start, estimate_tokens


# 每条消息固定 1 token 的精确 tokenizer，让测试的窗口边界完全确定
def _one_token(text: str) -> int:
    return 1


def _msg(role: str, content: str, tool_calls=None, tool_call_id=None) -> Message:
    return Message(
        role=role, content=content, tool_calls=tool_calls, tool_call_id=tool_call_id
    )


class _RecordingLlm(LlmService):
    """返回固定摘要，并记录每次 send_request 的用户消息内容。"""

    def __init__(self, summary: str = "SUMMARY") -> None:
        self.summary = summary
        self.calls: List[str] = []

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request.messages[0].content)
        return LlmResponse(content=self.summary, finish_reason="stop")

    async def stream_request(self, request: LlmRequest):
        yield LlmStreamChunk(content=self.summary, finish_reason="stop")

    async def validate_tools(self, tools):
        return []


# --------------------------------------------------------------------------- #
# token 估算
# --------------------------------------------------------------------------- #
def test_estimate_tokens_heuristics():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0
    # ASCII：4 字符 1 token
    assert estimate_tokens("abcd") == 1
    # 非 ASCII（中文）：1 字符 1 token
    assert estimate_tokens("你好") == 2
    # 精确 tokenizer 优先
    assert estimate_tokens("hello world", tokenizer=lambda t: 7) == 7


# --------------------------------------------------------------------------- #
# 滑动窗口
# --------------------------------------------------------------------------- #
async def test_sliding_window_trims_oldest_messages():
    messages = [
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
        _msg("assistant", "a2"),
    ]
    f = SlidingWindowFilter(max_tokens=2, tokenizer=_one_token)
    result = await f.filter_messages(messages)
    assert [m.content for m in result] == ["u2", "a2"]


async def test_sliding_window_preserves_system_messages():
    messages = [
        _msg("system", "sys"),
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
        _msg("assistant", "a2"),
    ]
    f = SlidingWindowFilter(max_tokens=2, tokenizer=_one_token)
    result = await f.filter_messages(messages)
    assert [m.content for m in result] == ["sys", "u2", "a2"]


async def test_sliding_window_protocol_integrity_no_orphan_tool():
    """窗口起点不能是孤立的 tool 消息，须补回其所属 assistant(tool_calls)。"""
    tc = ToolCall(id="t1", name="echo", arguments={})
    messages = [
        _msg("user", "u1"),
        _msg("assistant", "call", tool_calls=[tc]),
        _msg("tool", "result", tool_call_id="t1"),
        _msg("assistant", "final"),
    ]
    f = SlidingWindowFilter(max_tokens=2, tokenizer=_one_token)
    result = await f.filter_messages(messages)
    # 窗口从 assistant(call) 开始，保证 tool 结果不孤立
    assert result[0].role == "assistant"
    assert result[0].tool_calls and result[0].tool_calls[0].id == "t1"
    assert result[1].role == "tool"
    assert result[2].content == "final"


def test_compute_window_start_keeps_at_least_one():
    # 单条消息也超预算时，仍至少保留最后一条
    messages = [_msg("user", "u1"), _msg("assistant", "a1")]
    start = compute_window_start(messages, max_tokens=0, tokenizer=_one_token)
    assert start == 1
    assert messages[start:][0].content == "a1"


# --------------------------------------------------------------------------- #
# 总结压缩
# --------------------------------------------------------------------------- #
async def test_summary_buffer_compresses_old_and_keeps_recent():
    messages = [
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
        _msg("assistant", "a2"),
        _msg("user", "u3"),
        _msg("assistant", "a3"),
    ]
    f = SummaryBufferFilter(
        llm_service=_RecordingLlm(summary="SUMMARY"), max_tokens=2, tokenizer=_one_token
    )
    result = await f.filter_messages(messages)

    # 首条为注入的摘要消息，其后是保留的最近窗口
    assert result[0].role == "user"
    assert "SUMMARY" in result[0].content
    assert [m.content for m in result[1:]] == ["u3", "a3"]


async def test_summary_buffer_is_incremental():
    """同一会话第二次调用只压缩「新被裁剪」的消息，且合并进既有摘要。"""
    llm = _RecordingLlm(summary="SUMMARY2")
    f = SummaryBufferFilter(llm_service=llm, max_tokens=2, tokenizer=_one_token)

    messages = [
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
        _msg("assistant", "a2"),
        _msg("user", "u3"),
        _msg("assistant", "a3"),
    ]
    await f.filter_messages(messages)
    assert len(llm.calls) == 1

    # 追加一轮，首次消息不变 => 会话 key 稳定
    messages.append(_msg("user", "u4"))
    messages.append(_msg("assistant", "a4"))
    await f.filter_messages(messages)
    assert len(llm.calls) == 2

    second_call = llm.calls[1]
    # 第二次压缩合并了既有摘要与新被裁剪的消息，且不重复旧消息
    assert "SUMMARY" in second_call or "SUMMARY2" in second_call
    assert "u3" in second_call and "a3" in second_call
    assert "u1" not in second_call and "a1" not in second_call


async def test_summary_buffer_noop_when_within_budget():
    llm = _RecordingLlm()
    f = SummaryBufferFilter(llm_service=llm, max_tokens=100, tokenizer=_one_token)
    messages = [_msg("user", "u1"), _msg("assistant", "a1")]
    result = await f.filter_messages(messages)
    # 未超预算：不压缩、不调用 LLM，原样返回
    assert llm.calls == []
    assert result == messages


async def test_summary_buffer_fallback_on_llm_error():
    class _FailingLlm(_RecordingLlm):
        async def send_request(self, request: LlmRequest) -> LlmResponse:
            raise RuntimeError("boom")

    messages = [
        _msg("user", "u1"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
        _msg("assistant", "a2"),
    ]
    f = SummaryBufferFilter(
        llm_service=_FailingLlm(), max_tokens=2, tokenizer=_one_token, fallback_on_error=True
    )
    result = await f.filter_messages(messages)
    # 兜底：摘要消息仍存在，且保留最近窗口
    assert result[0].role == "user"
    assert [m.content for m in result[1:]] == ["u2", "a2"]
