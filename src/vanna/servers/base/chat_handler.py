"""
Framework-agnostic chat handling logic.

本模块是 HTTP 层与 Agent 之间的核心桥梁，且与具体 Web 框架无关。ChatHandler 的
唯一职责：把框架中立的 ChatRequest 交给 Agent.send_message，并把 Agent 产出的
UiComponent 逐个包装成 ChatStreamChunk，供上层（FastAPI/Flask）编码为具体传输格式。
它是整条 Text2SQL 链路中「Agent 唯一入口」的实际调用点。
"""

import asyncio
import logging
import uuid
from typing import AsyncGenerator, List, Optional

from ...core import Agent
from .models import ChatRequest, ChatResponse, ChatStreamChunk

logger = logging.getLogger(__name__)


class ChatHandler:
    """Core chat handling logic - framework agnostic.

    框架无关的聊天处理层。持有一个 Agent 实例；上层 Web 框架只需调用 handle_stream
    或 handle_poll，不必关心 Agent 内部如何编排 LLM / 工具 / 会话。
    """

    def __init__(
        self,
        agent: Agent,
    ):
        """Initialize chat handler.

        Args:
            agent: The agent to handle chat requests
        """
        self.agent = agent

    async def handle_stream(
        self,
        request: ChatRequest,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[ChatStreamChunk, None]:
        """Stream chat responses.

        Args:
            request: Chat request
            cancel_event: Optional asyncio.Event; when set, aborts the in-flight LLM
                stream and stops producing chunks (cooperative cancellation). The caller
                (transport layer) detects client disconnect or an explicit cancel request,
                sets this event, and handles the resulting asyncio.CancelledError.

        Yields:
            Chat stream chunks

        Agent 的唯一入口（流式版）：生成 conversation_id / request_id 后，逐块消费
        Agent.send_message 产出的 UiComponent，并转成 ChatStreamChunk 向上传递。
        cancel_event 原样透传给 Agent；Agent 在 token 级流式循环里监听它，一旦置位即
        中止底层 LLM 调用并向这里抛出 asyncio.CancelledError。
        """
        conversation_id = await self._resolve_conversation_id(request)
        # Use request_id from client for tracking, or use the one generated internally
        # request_id 优先复用客户端传来的（用于端到端追踪），否则内部生成。
        request_id = request.request_id or str(uuid.uuid4())

        async for component in self.agent.send_message(
            request_context=request.request_context,
            message=request.message,
            conversation_id=conversation_id,
            cancel_event=cancel_event,
        ):
            yield ChatStreamChunk.from_component(component, conversation_id, request_id)

    async def handle_poll(self, request: ChatRequest) -> ChatResponse:
        """Handle polling-based chat.

        Args:
            request: Chat request

        Returns:
            Complete chat response

        轮询版：完整消费整个流并聚合成单个 ChatResponse，等价于 handle_stream 的
        同步收集，适合不支持 SSE 的客户端。
        """
        chunks = []
        async for chunk in self.handle_stream(request):
            chunks.append(chunk)

        return ChatResponse.from_chunks(chunks)

    async def _resolve_conversation_id(self, request: ChatRequest) -> str:
        """Resolve a safe conversation id for an incoming request.

        客户端传入的 conversation_id 只有在「已存在且属于当前用户」时才复用；
        否则生成新的服务端 id，避免跨用户会话注入/接管。没有 request_context
        或归属校验失败时，同样退回服务端生成。
        """
        requested = request.conversation_id
        if not requested or request.request_context is None:
            return self._generate_conversation_id()

        try:
            user = await self.agent.user_resolver.resolve_user(
                request.request_context
            )
            existing = await self.agent.conversation_store.get_conversation(
                requested, user
            )
        except Exception:
            logger.warning(
                "Failed to verify conversation ownership for %r; issuing a new id",
                requested,
                exc_info=True,
            )
            return self._generate_conversation_id()

        if existing is None:
            logger.warning(
                "Rejecting unowned conversation_id %r for user %s; issuing a new id",
                requested,
                user.id,
            )
            return self._generate_conversation_id()

        return requested

    def _generate_conversation_id(self) -> str:
        """Generate new conversation ID.

        生成形如 conv_<8位hex> 的会话 ID；仅在客户端未提供 conversation_id 时使用。
        """
        return f"conv_{uuid.uuid4().hex[:8]}"
