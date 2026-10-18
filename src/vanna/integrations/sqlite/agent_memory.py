"""
SQLite-backed agent memory.

本模块提供内置的持久化智能体记忆：用标准库 sqlite3 落盘，重启不丢「工具使用」与
「文本」记忆。所有读写都带身份作用域（user_id / tenant_id），实现多用户（多租户）隔离。
检索沿用与 DemoAgentMemory 一致的轻量相似度（Jaccard + difflib），无需向量库依赖；
需要语义检索时可替换为 Chroma/Qdrant 等实现。

与 DemoAgentMemory 的关系：Demo 仍是零配置、内存态、方便本地测试的默认选择；本实现
是「需要持久化与多租户隔离」时的开箱选项，二者实现同一 AgentMemory 接口，可自由切换。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from vanna.capabilities.agent_memory import (
    AgentMemory,
    TextMemory,
    TextMemorySearchResult,
    ToolMemory,
    ToolMemorySearchResult,
)
from vanna.core.tool import ToolContext
from vanna.core.user import User

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    question TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    timestamp TEXT,
    success INTEGER NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_memories_user
    ON tool_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_tool_memories_tenant
    ON tool_memories(tenant_id);

CREATE TABLE IF NOT EXISTS text_memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_text_memories_user
    ON text_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_text_memories_tenant
    ON text_memories(tenant_id);
"""


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _tokenize(text: str) -> set[str]:
    return set(text.lower().split())


def _similarity(a: str, b: str) -> float:
    """Cheap similarity: max(Jaccard over tokens, difflib ratio)."""
    a_norm, b_norm = _normalize(a), _normalize(b)
    ta, tb = _tokenize(a_norm), _tokenize(b_norm)
    if not ta and not tb:
        jaccard = 1.0
    elif not ta or not tb:
        jaccard = 0.0
    else:
        jaccard = len(ta & tb) / max(1, len(ta | tb))
    ratio = difflib.SequenceMatcher(None, a_norm, b_norm).ratio()
    return max(jaccard, ratio)


class SQLiteAgentMemory(AgentMemory):
    """Persistent, multi-tenant agent memory backed by SQLite.

    Args:
        database_path: Path to the SQLite database file (created if missing).
        tenant_resolver: Optional callable(User) -> str deriving a tenant id.
            Defaults to the user id.
        share_within_tenant: If True, isolation is by tenant id (users in the same
            tenant share memories). Defaults to False (per-user isolation).

    Example:
        memory = SQLiteAgentMemory("vanna.db")
        agent = Agent(..., agent_memory=memory)
    """

    def __init__(
        self,
        database_path: str = "vanna_memory.db",
        *,
        tenant_resolver: Optional[Callable[[User], str]] = None,
        share_within_tenant: bool = False,
    ) -> None:
        self.database_path = database_path
        self._tenant_resolver: Callable[[User], str] = tenant_resolver or (
            lambda user: user.id
        )
        self._share_within_tenant = share_within_tenant
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()

    def _tenant_of(self, user: User) -> str:
        try:
            return str(self._tenant_resolver(user))
        except Exception:
            return user.id

    def _scope(self, context: ToolContext) -> Tuple[str, Any]:
        if self._share_within_tenant:
            return "tenant_id", self._tenant_of(context.user)
        return "user_id", context.user.id

    @staticmethod
    def _row_to_tool_memory(row: sqlite3.Row) -> ToolMemory:
        return ToolMemory(
            memory_id=row["memory_id"],
            question=row["question"],
            tool_name=row["tool_name"],
            args=json.loads(row["args_json"]),
            timestamp=row["timestamp"],
            success=bool(row["success"]),
            metadata=json.loads(row["metadata_json"])
            if row["metadata_json"]
            else {},
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
        )

    @staticmethod
    def _row_to_text_memory(row: sqlite3.Row) -> TextMemory:
        return TextMemory(
            memory_id=row["memory_id"],
            content=row["content"],
            timestamp=row["timestamp"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --------------------------------------------------------------- sync impls

    def _save_tool_usage_sync(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        user: User,
        success: bool,
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tool_memories
                    (memory_id, user_id, tenant_id, question, tool_name,
                     args_json, timestamp, success, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    user.id,
                    self._tenant_of(user),
                    question,
                    tool_name,
                    json.dumps(args, ensure_ascii=False, default=str),
                    self._now_iso(),
                    1 if success else 0,
                    json.dumps(metadata or {}, ensure_ascii=False, default=str),
                ),
            )
            self._conn.commit()

    def _save_text_memory_sync(self, content: str, user: User) -> TextMemory:
        memory = TextMemory(
            memory_id=str(uuid.uuid4()),
            content=content,
            timestamp=self._now_iso(),
            user_id=user.id,
            tenant_id=self._tenant_of(user),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO text_memories
                    (memory_id, user_id, tenant_id, content, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id,
                    memory.user_id,
                    memory.tenant_id,
                    memory.content,
                    memory.timestamp,
                ),
            )
            self._conn.commit()
        return memory

    def _search_similar_usage_sync(
        self,
        question: str,
        context: ToolContext,
        limit: int,
        similarity_threshold: float,
        tool_name_filter: Optional[str],
    ) -> List[ToolMemorySearchResult]:
        scope_col, scope_val = self._scope(context)
        query = f"SELECT * FROM tool_memories WHERE {scope_col} = ? AND success = 1"
        params: List[Any] = [scope_val]
        if tool_name_filter:
            query += " AND tool_name = ?"
            params.append(tool_name_filter)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        normalized_question = _normalize(question)
        scored: List[Tuple[ToolMemory, float]] = []
        for row in rows:
            memory = self._row_to_tool_memory(row)
            score = min(_similarity(normalized_question, memory.question), 1.0)
            if score >= similarity_threshold:
                scored.append((memory, score))
        scored.sort(key=lambda item: item[1], reverse=True)

        return [
            ToolMemorySearchResult(memory=memory, similarity_score=score, rank=rank)
            for rank, (memory, score) in enumerate(scored[:limit], start=1)
        ]

    def _search_text_memories_sync(
        self,
        query: str,
        context: ToolContext,
        limit: int,
        similarity_threshold: float,
    ) -> List[TextMemorySearchResult]:
        scope_col, scope_val = self._scope(context)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM text_memories WHERE {scope_col} = ?", (scope_val,)
            ).fetchall()

        normalized_query = _normalize(query)
        scored: List[Tuple[TextMemory, float]] = []
        for row in rows:
            memory = self._row_to_text_memory(row)
            score = min(_similarity(normalized_query, memory.content), 1.0)
            if score >= similarity_threshold:
                scored.append((memory, score))
        scored.sort(key=lambda item: item[1], reverse=True)

        return [
            TextMemorySearchResult(memory=memory, similarity_score=score, rank=rank)
            for rank, (memory, score) in enumerate(scored[:limit], start=1)
        ]

    def _get_recent_memories_sync(
        self, context: ToolContext, limit: int
    ) -> List[ToolMemory]:
        scope_col, scope_val = self._scope(context)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM tool_memories WHERE {scope_col} = ? "
                "ORDER BY rowid DESC LIMIT ?",
                (scope_val, limit),
            ).fetchall()
        return [self._row_to_tool_memory(row) for row in rows]

    def _get_recent_text_memories_sync(
        self, context: ToolContext, limit: int
    ) -> List[TextMemory]:
        scope_col, scope_val = self._scope(context)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM text_memories WHERE {scope_col} = ? "
                "ORDER BY rowid DESC LIMIT ?",
                (scope_val, limit),
            ).fetchall()
        return [self._row_to_text_memory(row) for row in rows]

    def _delete_by_id_sync(self, context: ToolContext, memory_id: str) -> bool:
        scope_col, scope_val = self._scope(context)
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM tool_memories WHERE memory_id = ? AND {scope_col} = ?",
                (memory_id, scope_val),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def _delete_text_memory_sync(self, context: ToolContext, memory_id: str) -> bool:
        scope_col, scope_val = self._scope(context)
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM text_memories WHERE memory_id = ? AND {scope_col} = ?",
                (memory_id, scope_val),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def _clear_memories_sync(
        self,
        context: ToolContext,
        tool_name: Optional[str],
        before_date: Optional[str],
    ) -> int:
        scope_col, scope_val = self._scope(context)

        tool_clauses = [f"{scope_col} = ?"]
        tool_params: List[Any] = [scope_val]
        if tool_name:
            tool_clauses.append("tool_name = ?")
            tool_params.append(tool_name)
        if before_date:
            tool_clauses.append("(timestamp IS NULL OR timestamp < ?)")
            tool_params.append(before_date)

        text_clauses = [f"{scope_col} = ?"]
        text_params: List[Any] = [scope_val]
        # 按工具名清理时不动文本记忆（与 DemoAgentMemory 行为一致）
        if tool_name is None and before_date:
            text_clauses.append("(timestamp IS NULL OR timestamp < ?)")
            text_params.append(before_date)

        with self._lock:
            tool_cursor = self._conn.execute(
                f"DELETE FROM tool_memories WHERE {' AND '.join(tool_clauses)}",
                tool_params,
            )
            deleted_tool = tool_cursor.rowcount if tool_cursor.rowcount > 0 else 0

            deleted_text = 0
            if tool_name is None:
                text_cursor = self._conn.execute(
                    f"DELETE FROM text_memories WHERE {' AND '.join(text_clauses)}",
                    text_params,
                )
                deleted_text = text_cursor.rowcount if text_cursor.rowcount > 0 else 0

            self._conn.commit()
            return deleted_tool + deleted_text

    # -------------------------------------------------------------- async facade

    async def save_tool_usage(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolContext,
        success: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist a tool usage pattern, scoped to the current user/tenant."""
        await asyncio.to_thread(
            self._save_tool_usage_sync,
            question,
            tool_name,
            args,
            context.user,
            success,
            metadata,
        )

    async def save_text_memory(self, content: str, context: ToolContext) -> TextMemory:
        """Persist a free-form text memory, scoped to the current user/tenant."""
        return await asyncio.to_thread(
            self._save_text_memory_sync, content, context.user
        )

    async def search_similar_usage(
        self,
        question: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        tool_name_filter: Optional[str] = None,
    ) -> List[ToolMemorySearchResult]:
        """Search similar tool usage patterns within the current user/tenant."""
        return await asyncio.to_thread(
            self._search_similar_usage_sync,
            question,
            context,
            limit,
            similarity_threshold,
            tool_name_filter,
        )

    async def search_text_memories(
        self,
        query: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> List[TextMemorySearchResult]:
        """Search text memories within the current user/tenant."""
        return await asyncio.to_thread(
            self._search_text_memories_sync,
            query,
            context,
            limit,
            similarity_threshold,
        )

    async def get_recent_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[ToolMemory]:
        """Get recently added memories (most recent first)."""
        return await asyncio.to_thread(
            self._get_recent_memories_sync, context, limit
        )

    async def get_recent_text_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[TextMemory]:
        """Get recently added text memories (most recent first)."""
        return await asyncio.to_thread(
            self._get_recent_text_memories_sync, context, limit
        )

    async def delete_by_id(self, context: ToolContext, memory_id: str) -> bool:
        """Delete a tool memory by ID (only if owned by the current user/tenant)."""
        return await asyncio.to_thread(self._delete_by_id_sync, context, memory_id)

    async def delete_text_memory(
        self, context: ToolContext, memory_id: str
    ) -> bool:
        """Delete a text memory by ID (only if owned by the current user/tenant)."""
        return await asyncio.to_thread(
            self._delete_text_memory_sync, context, memory_id
        )

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        """Clear memories within the current user/tenant. Returns deleted count."""
        return await asyncio.to_thread(
            self._clear_memories_sync, context, tool_name, before_date
        )
