"""
Base LLM middleware interface.

Middleware allows you to intercept and transform LLM requests and responses
for caching, monitoring, content filtering, and more.

本模块定义 LLM 中间件接口。它在 LLM 调用的「前」「后」各设一道拦截点，用于缓存、
计费、内容过滤、降级等横切逻辑，是围绕模型调用的洋葱式扩展点。
"""

from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import LlmRequest, LlmResponse


class LlmMiddleware(ABC):
    """Middleware for intercepting LLM requests and responses.

    Subclass this to create custom middleware that can:
    - Cache LLM responses
    - Log requests/responses
    - Filter or modify content
    - Track costs and usage
    - Implement fallback strategies

    Example:
        class CachingMiddleware(LlmMiddleware):
            def __init__(self):
                self.cache = {}

            async def before_llm_request(self, request: LlmRequest) -> LlmRequest:
                # Could check cache here
                return request

            async def after_llm_response(self, request: LlmRequest, response: LlmResponse) -> LlmResponse:
                # Cache the response
                cache_key = self._compute_key(request)
                self.cache[cache_key] = response
                return response

        agent = AgentRunner(
            llm_service=...,
            llm_middlewares=[CachingMiddleware(), LoggingMiddleware()]
        )

    LLM 中间件的抽象基类。before_llm_request / after_llm_response 均采用「返回式」
    语义：返回原对象即不干预，返回新对象即替换。流式与非流式路径都会经过它们
    （见 agent._send_llm_request 与 _handle_streaming_response）。
    """

    async def before_llm_request(self, request: "LlmRequest") -> "LlmRequest":
        """Called before sending request to LLM.

        Args:
            request: The LLM request about to be sent

        Returns:
            Modified request, or original if no changes

        在发往 LLM 之前调用，可改写请求（如注入额外上下文、改写提示词）；返回
        原对象表示不干预。
        """
        return request

    async def after_llm_response(
        self, request: "LlmRequest", response: "LlmResponse"
    ) -> "LlmResponse":
        """Called after receiving response from LLM.

        Args:
            request: The original request
            response: The LLM response

        Returns:
            Modified response, or original if no changes

        在收到 LLM 响应之后调用，可改写响应（如过滤违规内容、缓存结果、计费）；
        返回原对象表示不干预。
        """
        return response
