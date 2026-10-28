"""Read-path safety wrapper for AgentMemory backends."""

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

    Reads (search/recent) return an empty result and log a warning when the
    backend is unavailable. Writes and deletes still delegate normally so an
    admin save/delete does not silently claim success.
    """

    def __init__(self, backend: AgentMemory) -> None:
        self._backend = backend

    async def search_text_memories(
        self,
        query: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> List[TextMemorySearchResult]:
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
        await self._backend.save_tool_usage(
            question,
            tool_name,
            args,
            context,
            success=success,
            metadata=metadata,
        )

    async def save_text_memory(self, content: str, context: ToolContext) -> TextMemory:
        return await self._backend.save_text_memory(content, context)

    async def delete_by_id(self, context: ToolContext, memory_id: str) -> bool:
        return await self._backend.delete_by_id(context, memory_id)

    async def delete_text_memory(self, context: ToolContext, memory_id: str) -> bool:
        return await self._backend.delete_text_memory(context, memory_id)

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        return await self._backend.clear_memories(
            context,
            tool_name=tool_name,
            before_date=before_date,
        )
