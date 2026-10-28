"""AgentMemory 读路径降级包装。

记忆是增强能力：Chroma/Qdrant 短暂不可用时，Agent 仍应能依靠静态提示词
完成回答。因此本包装把**读操作**降级为空结果，绝不让检索异常中断对话。

写操作和删除操作故意不吞异常：管理员保存/删除时必须得到真实错误，
不能返回“成功”但实际没写到后端。
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
    """让检索失败不致命，同时保留写操作的显式失败。

    设计边界：

    - ``search_*`` / ``get_recent_*``：失败返回空列表并记录 warning；
    - ``save_*`` / ``delete_*`` / ``clear_*``：原样委托，失败向上抛；
    - 不改变记忆内容，只负责异常隔离。

    这样即使向量库宕机，Agent 仍可退回 Stage 2 的静态提示词行为。
    """

    def __init__(self, backend: AgentMemory) -> None:
        """包装已有后端；构造阶段不执行任何 I/O。"""
        self._backend = backend

    async def search_text_memories(
        self,
        query: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> List[TextMemorySearchResult]:
        """检索文本知识；后端不可用时返回空列表。"""
        try:
            return await self._backend.search_text_memories(
                query,
                context,
                limit=limit,
                similarity_threshold=similarity_threshold,
            )
        except Exception:
            logger.warning("文本记忆检索失败，跳过本次检索", exc_info=True)
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
        """检索相似 SQL 模式；不让检索异常阻断 Agent 主循环。"""
        try:
            return await self._backend.search_similar_usage(
                question,
                context,
                limit=limit,
                similarity_threshold=similarity_threshold,
                tool_name_filter=tool_name_filter,
            )
        except Exception:
            logger.warning("工具记忆检索失败，跳过本次检索", exc_info=True)
            return []

    async def get_recent_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[ToolMemory]:
        """返回最近的工具记忆；读失败时返回空列表。"""
        try:
            return await self._backend.get_recent_memories(context, limit=limit)
        except Exception:
            logger.warning("最近工具记忆读取失败，跳过本次读取", exc_info=True)
            return []

    async def get_recent_text_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[TextMemory]:
        """返回最近的文本记忆；读失败时返回空列表。"""
        try:
            return await self._backend.get_recent_text_memories(context, limit=limit)
        except Exception:
            logger.warning(
                "最近文本记忆读取失败，跳过本次读取",
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
        """委托写操作；失败必须让调用方感知，不能吞掉。"""
        await self._backend.save_tool_usage(
            question,
            tool_name,
            args,
            context,
            success=success,
            metadata=metadata,
        )

    async def save_text_memory(self, content: str, context: ToolContext) -> TextMemory:
        """委托文本记忆写入；失败不隐藏。"""
        return await self._backend.save_text_memory(content, context)

    async def delete_by_id(self, context: ToolContext, memory_id: str) -> bool:
        """委托删除；只有后端明确返回未找到时才返回 False。"""
        return await self._backend.delete_by_id(context, memory_id)

    async def delete_text_memory(self, context: ToolContext, memory_id: str) -> bool:
        """委托文本记忆删除；失败不隐藏。"""
        return await self._backend.delete_text_memory(context, memory_id)

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        """委托批量清理；失败必须暴露给管理员调用方。"""
        return await self._backend.clear_memories(
            context,
            tool_name=tool_name,
            before_date=before_date,
        )
