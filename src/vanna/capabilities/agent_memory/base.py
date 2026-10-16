"""
Agent memory capability interface for tool usage learning.

This module contains the abstract base class for agent memory operations,
following the same pattern as the FileSystem interface.

本模块定义智能体记忆能力的抽象接口 AgentMemory，是 Vanna「持续学习」的存储底座：
保存/检索两类记忆——「工具使用记忆」（问题 + 工具 + 参数的成功模式）与「文本记忆」
（领域知识）。具体后端（内存、向量库、数据库）只需实现本接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from vanna.core.tool import ToolContext
    from .models import (
        ToolMemorySearchResult,
        TextMemory,
        TextMemorySearchResult,
        ToolMemory,
    )


class AgentMemory(ABC):
    """Abstract base class for agent memory operations.

    智能体记忆抽象基类。所有方法都以 context（含用户身份）为作用域，实现按用户
    隔离记忆。save_* 用于沉淀经验，search_* 用于检索相似历史，delete_*/clear_* 用于
    维护。Text2SQL 中，成功执行并保存的 SQL 模式会在下次相似问题时被检索复用。
    """

    @abstractmethod
    async def save_tool_usage(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: "ToolContext",
        success: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save a tool usage pattern for future reference.

        保存一次「工具使用」记忆（问题 + 工具名 + 参数 + 是否成功），供后续相似问题
        检索复用。Text2SQL 中即保存「问题 → 正确 SQL」的成功模式。
        """
        pass

    @abstractmethod
    async def save_text_memory(
        self, content: str, context: "ToolContext"
    ) -> "TextMemory":
        """Save a free-form text memory.

        保存一条自由文本记忆（如数据库 schema 说明、业务术语定义）。
        """
        pass

    @abstractmethod
    async def search_similar_usage(
        self,
        question: str,
        context: "ToolContext",
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        tool_name_filter: Optional[str] = None,
    ) -> List[ToolMemorySearchResult]:
        """Search for similar tool usage patterns based on a question.

        按问题检索相似的「工具使用」历史，可按工具名过滤、按相似度阈值裁剪。
        """
        pass

    @abstractmethod
    async def search_text_memories(
        self,
        query: str,
        context: "ToolContext",
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> List["TextMemorySearchResult"]:
        """Search stored text memories based on a query.

        按查询检索相似的文本记忆（被 DefaultLlmContextEnhancer 用作 RAG 知识注入）。
        """
        pass

    @abstractmethod
    async def get_recent_memories(
        self, context: "ToolContext", limit: int = 10
    ) -> List[ToolMemory]:
        """Get recently added memories. Returns most recent memories first.

        获取最近的工具使用记忆（新的在前），供 /memories 命令展示。
        """
        pass

    @abstractmethod
    async def get_recent_text_memories(
        self, context: "ToolContext", limit: int = 10
    ) -> List["TextMemory"]:
        """Fetch recently stored text memories.

        获取最近的文本记忆（新的在前）。
        """
        pass

    @abstractmethod
    async def delete_by_id(self, context: "ToolContext", memory_id: str) -> bool:
        """Delete a memory by its ID. Returns True if deleted, False if not found.

        按 ID 删除工具使用记忆，返回是否删除成功。
        """
        pass

    @abstractmethod
    async def delete_text_memory(self, context: "ToolContext", memory_id: str) -> bool:
        """Delete a text memory by its ID. Returns True if deleted, False if not found.

        按 ID 删除文本记忆，返回是否删除成功。
        """
        pass

    @abstractmethod
    async def clear_memories(
        self,
        context: "ToolContext",
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        """Clear stored memories (tool or text). Returns number of memories deleted.

        批量清空记忆（可按工具名/日期过滤），返回删除条数。
        """
        pass
