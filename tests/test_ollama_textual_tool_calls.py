"""
Tests for OllamaLlmService textual tool-call fallback.

qwen 等小模型有时把 tool call JSON 直接打进正文而非走 tool_calls 通道
（ollama 解析 <tool_call> 失败时会原样保留），验证兜底解析能正确认领、
且不会把普通 JSON 文本误判为工具调用。
"""

import pytest

from vanna.core.llm import LlmMessage, LlmRequest
from vanna.core.tool import ToolSchema
from vanna.core.user import User
from vanna.integrations.ollama import OllamaLlmService


class _FakeAsyncClient:
    """模拟 ollama AsyncClient：chat 返回预置的 chunk 序列。"""

    def __init__(self, chunks):
        self._chunks = chunks

    async def chat(self, **kwargs):
        if kwargs.get("stream"):

            async def gen():
                for c in self._chunks:
                    yield c

            return gen()

        return self._chunks[-1]


def _make_service(chunks) -> OllamaLlmService:
    service = OllamaLlmService(model="qwen2.5:7b", host="http://localhost:11434")
    service._client = _FakeAsyncClient(chunks)
    return service


def _make_request() -> LlmRequest:
    return LlmRequest(
        messages=[LlmMessage(role="user", content="list databases")],
        user=User(id="u", email="u@example.com", group_memberships=[]),
        tools=[
            ToolSchema(
                name="run_sql",
                description="Run SQL",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )


class TestStreamTextualToolCalls:
    async def test_json_tool_call_in_content_is_recovered(self):
        service = _make_service(
            [
                {"message": {"role": "assistant", "content": "我来查询。"}, "done": False},
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"name": "run_sql", "arguments": {"sql": "SHOW DATABASES"}}',
                    },
                    "done": False,
                },
                {
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "done_reason": "stop",
                },
            ]
        )
        chunks = [c async for c in service.stream_request(_make_request())]
        final = chunks[-1]
        assert final.tool_calls is not None
        assert final.tool_calls[0].name == "run_sql"
        assert final.tool_calls[0].arguments == {"sql": "SHOW DATABASES"}

    async def test_native_tool_calls_take_precedence(self):
        service = _make_service(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": "文字",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "run_sql",
                                    "arguments": {"sql": "SELECT 1"},
                                }
                            }
                        ],
                    },
                    "done": True,
                    "done_reason": "stop",
                }
            ]
        )
        chunks = [c async for c in service.stream_request(_make_request())]
        final = chunks[-1]
        assert final.tool_calls[0].name == "run_sql"
        assert final.tool_calls[0].id == "tool_call_0"

    async def test_unrelated_json_not_claimed(self):
        service = _make_service(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": '结果 {"name": "unknown_tool", "arguments": {}}',
                    },
                    "done": True,
                    "done_reason": "stop",
                }
            ]
        )
        chunks = [c async for c in service.stream_request(_make_request())]
        final = chunks[-1]
        assert final.tool_calls is None


class TestSendRequestTextualToolCalls:
    async def test_content_json_stripped_and_recovered(self):
        service = _make_service(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": '我来查询。{"name": "run_sql", "arguments": {"sql": "SHOW DATABASES"}}',
                    },
                    "done": True,
                    "done_reason": "stop",
                }
            ]
        )
        resp = await service.send_request(_make_request())
        assert resp.tool_calls is not None
        assert resp.tool_calls[0].name == "run_sql"
        assert resp.content == "我来查询。"
