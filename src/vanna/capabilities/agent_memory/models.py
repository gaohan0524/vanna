"""
Memory storage models and types.

本模块定义记忆存储的数据模型：工具使用记忆（ToolMemory）、文本记忆（TextMemory）、
两者的检索结果，以及记忆统计（MemoryStats）。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ToolMemory(BaseModel):
    """Represents a stored tool usage memory.

    一条「工具使用」记忆：记录某问题下用了哪个工具、传了什么参数、是否成功。
    Text2SQL 中即「问题 → SQL 参数」的成功模式。
    """

    memory_id: Optional[str] = None
    question: str
    tool_name: str
    args: Dict[str, Any]
    timestamp: Optional[str] = None
    success: bool = True
    metadata: Optional[Dict[str, Any]] = None
    # 归属用户（多租户/多用户隔离键）；由实现写入，用于检索时按身份过滤
    user_id: Optional[str] = None
    # 归属租户（可选，支持「组织内共享记忆」等更粗粒度隔离）
    tenant_id: Optional[str] = None


class TextMemory(BaseModel):
    """Represents a stored free-form text memory.

    一条自由文本记忆：如数据库字段含义、业务术语等领域知识。
    """

    memory_id: Optional[str] = None
    content: str
    timestamp: Optional[str] = None
    # 归属用户（多租户/多用户隔离键）
    user_id: Optional[str] = None
    # 归属租户（可选）
    tenant_id: Optional[str] = None


class ToolMemorySearchResult(BaseModel):
    """Represents a search result from tool memory storage.

    工具使用记忆的检索结果：命中记忆 + 相似度分 + 排名。
    """

    memory: ToolMemory
    similarity_score: float
    rank: int


class TextMemorySearchResult(BaseModel):
    """Represents a search result from text memory storage.

    文本记忆的检索结果：命中记忆 + 相似度分 + 排名。
    """

    memory: TextMemory
    similarity_score: float
    rank: int


class MemoryStats(BaseModel):
    """Memory storage statistics.

    记忆存储统计：总数、去重工具数、去重问题数、成功率、最常用工具分布。
    """

    total_memories: int
    unique_tools: int
    unique_questions: int
    success_rate: float
    most_used_tools: Dict[str, int]
