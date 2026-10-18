"""
Integrations module.

This package contains concrete implementations of core abstractions and capabilities.
"""

from .local import MemoryConversationStore
from .mock import MockLlmService
from .plotly import PlotlyChartGenerator
from .sqlite import (
    SQLiteAgentMemory,
    SQLiteConversationStore,
    SqliteRunner,
)
from .postgres import PostgresAgentMemory, PostgresConversationStore
from .mysql import MySQLAgentMemory, MySQLConversationStore
from .redis import RedisAgentMemory, RedisConversationStore

__all__ = [
    "MockLlmService",
    "MemoryConversationStore",
    # Persistent, multi-tenant-aware backends
    "SQLiteConversationStore",
    "SQLiteAgentMemory",
    "PostgresConversationStore",
    "PostgresAgentMemory",
    "MySQLConversationStore",
    "MySQLAgentMemory",
    "RedisConversationStore",
    "RedisAgentMemory",
    "SqliteRunner",
    "PlotlyChartGenerator",
]
