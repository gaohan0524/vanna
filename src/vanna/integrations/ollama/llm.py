"""
Ollama LLM service implementation.

This module provides an implementation of the LlmService interface backed by
Ollama's local LLM API. It supports non-streaming responses and streaming
of text content. Tool calling support depends on the Ollama model being used.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncGenerator, Dict, Iterator, List, Optional, Set, Tuple

from vanna.core.llm import (
    LlmService,
    LlmRequest,
    LlmResponse,
    LlmStreamChunk,
)
from vanna.core.tool import ToolCall, ToolSchema


def _iter_json_objects(text: str) -> Iterator[Tuple[int, int, Any]]:
    """扫描文本中所有平衡的 JSON 对象，产出 (start, end, obj) 位置区间。"""
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        start = text.find("{", pos)
        if start == -1:
            return
        try:
            obj, end = decoder.raw_decode(text, start)
            yield start, end, obj
            pos = end
        except Exception:
            pos = start + 1


def _normalize_tool_obj(obj: Any) -> Tuple[Optional[str], Any]:
    """归一化两种常见的文本 tool call 形态：

    {"name": ..., "arguments": {...}} 或 {"function": {"name": ..., "arguments": ...}}
    返回 (工具名, 参数)；不匹配时返回 (None, None)。
    """
    if not isinstance(obj, dict):
        return None, None
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else obj
    name = fn.get("name")
    if not isinstance(name, str) or not name:
        return None, None
    args = fn.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {"_raw": args}
    if not isinstance(args, dict):
        args = {"args": args}
    return name, args


class OllamaLlmService(LlmService):
    """Ollama-backed LLM service for local model inference.

    Args:
        model: Ollama model name (e.g., "gpt-oss:20b").
        host: Ollama server URL; defaults to "http://localhost:11434" or env `OLLAMA_HOST`.
        timeout: Request timeout in seconds; defaults to 240.
        num_ctx: Context window size; defaults to 8192.
        temperature: Sampling temperature; defaults to 0.7.
        extra_options: Additional options passed to Ollama (e.g., num_predict, top_k, top_p).
    """

    def __init__(
        self,
        model: str,
        host: Optional[str] = None,
        timeout: float = 240.0,
        num_ctx: int = 8192,
        temperature: float = 0.7,
        **extra_options: Any,
    ) -> None:
        try:
            import ollama
        except ImportError as e:
            raise ImportError(
                "ollama package is required. Install with: pip install 'vanna[ollama]' or pip install ollama"
            ) from e

        if not model:
            raise ValueError("model parameter is required for Ollama")

        self.model = model
        self.host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.extra_options = extra_options

        # Create Ollama async client
        self._client = ollama.AsyncClient(host=self.host, timeout=timeout)

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        """Send a non-streaming request to Ollama and return the response."""
        payload = self._build_payload(request)

        # 异步调用 Ollama API
        try:
            resp = await self._client.chat(**payload)
        except Exception as e:
            raise RuntimeError(f"Ollama request failed: {str(e)}") from e

        # Extract message from response
        message = resp.get("message", {})
        content = message.get("content")
        tool_calls = self._extract_tool_calls_from_message(message)

        # 文本形式工具调用兜底：小模型可能把 tool call JSON 直接打进正文
        if not tool_calls and content:
            cleaned, textual_calls = self._parse_textual_tool_calls(
                content, self._tool_names_from_request(request)
            )
            if textual_calls:
                tool_calls = textual_calls
                content = cleaned or None

        # Extract usage information if available
        usage: Dict[str, int] = {}
        if "prompt_eval_count" in resp or "eval_count" in resp:
            usage = {
                "prompt_tokens": resp.get("prompt_eval_count", 0),
                "completion_tokens": resp.get("eval_count", 0),
                "total_tokens": resp.get("prompt_eval_count", 0)
                + resp.get("eval_count", 0),
            }

        return LlmResponse(
            content=content,
            tool_calls=tool_calls or None,
            finish_reason=resp.get("done_reason")
            or ("stop" if resp.get("done") else None),
            usage=usage or None,
        )

    async def stream_request(
        self, request: LlmRequest
    ) -> AsyncGenerator[LlmStreamChunk, None]:
        """Stream a request to Ollama.

        Emits `LlmStreamChunk` for textual deltas as they arrive. Tool calls are
        accumulated and emitted in a final chunk when the stream ends.
        """
        payload = self._build_payload(request)

        # Ollama 异步流式
        try:
            stream = await self._client.chat(**payload, stream=True)
        except Exception as e:
            raise RuntimeError(f"Ollama streaming request failed: {str(e)}") from e

        # Accumulate tool calls if present
        accumulated_tool_calls: List[ToolCall] = []
        content_text = ""  # 累积正文，用于流结束后的文本形式工具调用兜底
        last_finish: Optional[str] = None
        usage: Optional[Dict[str, int]] = None

        async for chunk in stream:
            message = chunk.get("message", {})

            # Yield text content
            content = message.get("content")
            if content:
                content_text += content
                yield LlmStreamChunk(content=content)

            # Accumulate tool calls
            tool_calls = self._extract_tool_calls_from_message(message)
            if tool_calls:
                accumulated_tool_calls.extend(tool_calls)

            # Track finish reason + usage on terminal chunk
            if chunk.get("done"):
                last_finish = chunk.get("done_reason", "stop")
                usage = {
                    "prompt_tokens": chunk.get("prompt_eval_count", 0),
                    "completion_tokens": chunk.get("eval_count", 0),
                    "total_tokens": chunk.get("prompt_eval_count", 0)
                    + chunk.get("eval_count", 0),
                }

        # 文本形式工具调用兜底：小模型常把 tool call JSON 直接打进正文。
        # 注意：此时 JSON 已作为 token 流出（无法追溯），但解析出的工具调用
        # 会让工具循环正确继续执行，而不是把 JSON 当最终答案提前结束。
        if not accumulated_tool_calls and content_text:
            _, textual_calls = self._parse_textual_tool_calls(
                content_text, self._tool_names_from_request(request)
            )
            if textual_calls:
                accumulated_tool_calls.extend(textual_calls)

        # Emit final chunk with tool calls if any
        if accumulated_tool_calls:
            yield LlmStreamChunk(
                tool_calls=accumulated_tool_calls,
                finish_reason=last_finish or "stop",
                usage=usage,
            )
        else:
            # Emit terminal chunk to signal completion
            yield LlmStreamChunk(finish_reason=last_finish or "stop", usage=usage)

    async def validate_tools(self, tools: List[ToolSchema]) -> List[str]:
        """Validate tool schemas. Returns a list of error messages."""
        errors: List[str] = []
        # Basic validation; Ollama model support for tools varies
        for t in tools:
            if not t.name:
                errors.append(f"Tool must have a name")
            if not t.description:
                errors.append(f"Tool '{t.name}' should have a description")
        return errors

    # Internal helpers
    def _build_payload(self, request: LlmRequest) -> Dict[str, Any]:
        """Build the Ollama chat payload from LlmRequest."""
        messages: List[Dict[str, Any]] = []

        # Add system prompt as first message if provided
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})

        # Convert messages to Ollama format
        for m in request.messages:
            msg: Dict[str, Any] = {"role": m.role, "content": m.content or ""}

            # Handle tool calls in assistant messages
            if m.role == "assistant" and m.tool_calls:
                # Some Ollama models support tool_calls in message
                tool_calls_payload = []
                for tc in m.tool_calls:
                    tool_calls_payload.append(
                        {"function": {"name": tc.name, "arguments": tc.arguments}}
                    )
                msg["tool_calls"] = tool_calls_payload

            messages.append(msg)

        # Build tools array if tools are provided
        tools_payload: Optional[List[Dict[str, Any]]] = None
        if request.tools:
            tools_payload = []
            for t in request.tools:
                tools_payload.append(
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        },
                    }
                )

        # Build options
        options: Dict[str, Any] = {
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
            **self.extra_options,
        }

        # Build final payload
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "options": options,
        }

        # Add tools if provided (note: not all Ollama models support tools)
        if tools_payload:
            payload["tools"] = tools_payload

        # 结构化输出：Ollama 的 `format` 接受 "json" 或 JSON Schema
        if request.response_format:
            response_format = self._translate_response_format(request.response_format)
            if response_format is not None:
                payload["format"] = response_format

        return payload

    @staticmethod
    def _translate_response_format(response_format: Dict[str, Any]) -> Any:
        """把 provider-agnostic 的 response_format 翻译为 Ollama 的 format 参数。"""
        kind = response_format.get("type")
        if kind == "json_object":
            return "json"
        if kind == "json_schema":
            json_schema = response_format.get("json_schema") or {}
            if isinstance(json_schema, dict):
                schema = json_schema.get("schema")
                if schema:
                    return schema
            return "json"
        return None

    def _extract_tool_calls_from_message(
        self, message: Dict[str, Any]
    ) -> List[ToolCall]:
        """Extract tool calls from Ollama message."""
        tool_calls: List[ToolCall] = []

        # Check for tool_calls in message
        raw_tool_calls = message.get("tool_calls", [])
        if not raw_tool_calls:
            return tool_calls

        for idx, tc in enumerate(raw_tool_calls):
            fn = tc.get("function", {})
            name = fn.get("name")
            if not name:
                continue

            # Parse arguments
            arguments = fn.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {"_raw": arguments}

            if not isinstance(arguments, dict):
                arguments = {"args": arguments}

            tool_calls.append(
                ToolCall(
                    id=tc.get("id", f"tool_call_{idx}"),
                    name=name,
                    arguments=arguments,
                )
            )

        return tool_calls

    def _parse_textual_tool_calls(
        self, content: str, tool_names: Set[str]
    ) -> Tuple[str, List[ToolCall]]:
        """从模型正文里解析以 JSON 文本形式输出的工具调用（小模型兜底）。

        qwen 等模型有时不走 tool_calls 通道，而是把 {"name": ..., "arguments": ...}
        直接打进 content（ollama 解析 <tool_call> 失败时会原样保留在正文里）。
        仅当 JSON 的 name 与本次请求注册的工具名匹配时才认领，避免把普通 JSON
        输出误判为工具调用。返回 (剥离工具调用 JSON 后的剩余文本, 工具调用列表)。
        """
        if not content or not tool_names:
            return content, []

        spans: List[Tuple[int, int]] = []
        calls: List[ToolCall] = []
        for start, end, obj in _iter_json_objects(content):
            name, args = _normalize_tool_obj(obj)
            if name in tool_names:
                spans.append((start, end))
                calls.append(
                    ToolCall(
                        id=f"textual_{len(calls)}", name=name, arguments=args or {}
                    )
                )

        if not calls:
            return content, []

        # 剥离已认领的 JSON 区间，保留其余说明文字
        pieces: List[str] = []
        last = 0
        for start, end in spans:
            pieces.append(content[last:start])
            last = end
        pieces.append(content[last:])
        cleaned = "".join(pieces).strip()
        return cleaned, calls

    @staticmethod
    def _tool_names_from_request(request: LlmRequest) -> Set[str]:
        """提取本次请求注册的工具名集合（用于文本形式 tool call 校验）。"""
        return {
            t.name
            for t in (request.tools or [])
            if getattr(t, "name", None)
        }
