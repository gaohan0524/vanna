"""
LLM domain interface.

This module contains the abstract base class for LLM services.

本模块定义大模型服务的抽象基类 LlmService，是「换模型供应商」的唯一契约：任何厂商
（Anthropic / OpenAI / Ollama / 本地模型 / Mock）只需实现三个方法，即可被 Agent 无缝
使用，框架其余部分完全不感知供应商差异。
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, List

from .models import LlmRequest, LlmResponse, LlmStreamChunk


class LlmService(ABC):
    """Service for LLM communication.

    大模型服务抽象基类。send_request 与 stream_request 是两条等价路径（一次性 / 流式），
    validate_tools 用于让供应商在调用前校验工具定义是否被其协议支持。
    """

    @abstractmethod
    async def send_request(self, request: LlmRequest) -> LlmResponse:
        """Send a request to the LLM.

        一次性（非流式）调用：完整返回一个 LlmResponse。适合 stream 关闭时的场景。
        """
        pass

    @abstractmethod
    async def stream_request(
        self, request: LlmRequest
    ) -> AsyncGenerator[LlmStreamChunk, None]:
        """Stream a request to the LLM.

        Args:
            request: The LLM request to stream

        Yields:
            LlmStreamChunk instances as they arrive

        流式调用：以异步生成器逐块产出 LlmStreamChunk。Agent 会把这些分片聚合成一个
        完整 LlmResponse 后再进入工具循环判定（当前非 token 级推送）。
        """
        # This is an async generator method
        # 先 raise 再 yield 是让该方法成为「异步生成器」的惯用写法，抽象方法本身不会被调用
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator

    @abstractmethod
    async def validate_tools(self, tools: List[Any]) -> List[str]:
        """Validate tool schemas and return any errors.

        校验工具 schema 是否被该供应商支持；返回错误描述列表（空列表表示全部通过）。
        用于在发起请求前发现「工具定义不兼容」问题，避免运行时才报错。
        """
        pass
