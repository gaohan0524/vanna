"""Read-path safety wrapper for AgentMemory backends.

Memory is an optional enhancement: if Chroma/Qdrant is temporarily unavailable,
the agent should still be able to answer from the static prompt.  This wrapper
therefore degrades **reads** to an empty result and never lets a retrieval
exception abort the conversation.

Writes and deletes are intentionally not swallowed: an admin save/delete must
receive a real error instead of a false "success".
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from vanna.capabilities.agent_memory import (
    AgentMemory,
    TextMemory,
    TextMemorySearchResult,
    ToolMemory,
    ToolMemorySearchResult,
)
from vanna.core.tool import ToolContext

logger = logging.getLogger(__name__)


class SafeAgentMemory(AgentMemory):
    """Make retrieval failures non-fatal while keeping writes explicit.

    设计边界：

    - ``search_*`` / ``get_recent_*``：失败返回空列表，记录 warning；
    - ``save_*`` / ``delete_*`` / ``clear_*``：原样委托，失败向上抛；
    - 不改变记忆内容，只负责异常隔离。

    这样即使向量库宕机，Agent 仍可退回 Stage 2 的静态 prompt 行为。
    """

    def __init__(self, backend: AgentMemory) -> None:
        """Wrap an existing backend; construction performs no I/O."""
        self._backend = backend

    async def search_text_memories(
        self,
        query: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> List[TextMemorySearchResult]:
        """Retrieve text knowledge; return [] if the backend is unavailable."""
        try:
            return await self._backend.search_text_memories(
                query,
                context,
                limit=limit,
                similarity_threshold=similarity_threshold,
            )
        except Exception:
            logger.warning(
                "Text-memory retrieval failed; continuing without it", exc_info=True
            )
            return []

    async def search_similar_usage(
        self,
        question: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        tool_name_filter: Optional[str] = None,
    ) -> List[ToolMemorySearchResult]:
        """Retrieve similar SQL patterns; never block the main agent loop."""
        try:
            return await self._backend.search_similar_usage(
                question,
                context,
                limit=limit,
                similarity_threshold=similarity_threshold,
                tool_name_filter=tool_name_filter,
            )
        except Exception:
            logger.warning(
                "Tool-memory retrieval failed; continuing without it", exc_info=True
            )
            return []

    async def get_recent_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[ToolMemory]:
        """Return recent tool memories, or [] if the backend cannot be read."""
        try:
            return await self._backend.get_recent_memories(context, limit=limit)
        except Exception:
            logger.warning(
                "Recent-memory lookup failed; continuing without it", exc_info=True
            )
            return []

    async def get_recent_text_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[TextMemory]:
        """Return recent text memories, or [] if the backend cannot be read."""
        try:
            return await self._backend.get_recent_text_memories(context, limit=limit)
        except Exception:
            logger.warning(
                "Recent text-memory lookup failed; continuing without it",
                exc_info=True,
            )
            return []

    async def save_tool_usage(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolContext,
        success: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Delegate a write; errors are intentionally visible to the caller."""
        await self._backend.save_tool_usage(
            question,
            tool_name,
            args,
            context,
            success=success,
            metadata=metadata,
        )

    async def save_text_memory(self, content: str, context: ToolContext) -> TextMemory:
        """Delegate a text-memory write; failures are not hidden."""
        return await self._backend.save_text_memory(content, context)

    async def delete_by_id(self, context: ToolContext, memory_id: str) -> bool:
        """Delegate deletion; return False only when the backend reports it."""
        return await self._backend.delete_by_id(context, memory_id)

    async def delete_text_memory(self, context: ToolContext, memory_id: str) -> bool:
        """Delegate text-memory deletion; failures are not hidden."""
        return await self._backend.delete_text_memory(context, memory_id)

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        """Delegate bulk cleanup; a failure is visible to the admin caller."""
        return await self._backend.clear_memories(
            context,
            tool_name=tool_name,
            before_date=before_date,
        )
