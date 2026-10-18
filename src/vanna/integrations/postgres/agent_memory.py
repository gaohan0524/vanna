"""
PostgreSQL-backed agent memory.

本模块提供 PostgreSQL 持久化智能体记忆，复用 psycopg2 依赖，所有读写按 user_id /
tenant_id 作用域隔离。检索使用与 DemoAgentMemory 一致的轻量相似度（Jaccard + difflib）。
"""

from __future__ import annotations

import asyncio
import difflib
import json
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
CREATE TABLE IF NOT EXISTS vanna_tool_memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    question TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    timestamp TEXT,
    success BOOLEAN NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_vanna_tool_memories_user
    ON vanna_tool_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_vanna_tool_memories_tenant
    ON vanna_tool_memories(tenant_id);

CREATE TABLE IF NOT EXISTS vanna_text_memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_vanna_text_memories_user
    ON vanna_text_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_vanna_text_memories_tenant
    ON vanna_text_memories(tenant_id);
"""


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _tokenize(text: str) -> set[str]:
    return set(text.lower().split())


def _similarity(a: str, b: str) -> float:
    a_norm, b_norm = _normalize(a), _normalize(b)
    ta, tb = _tokenize(a_norm), _tokenize(b_norm)
    if not ta and not tb:
        jaccard = 1.0
    elif not ta or not tb:
        jaccard = 0.0
    else:
        jaccard = len(ta & tb) / max(1, len(ta | tb))
    return max(jaccard, difflib.SequenceMatcher(None, a_norm, b_norm).ratio())


class PostgresAgentMemory(AgentMemory):
    """Persistent, multi-tenant agent memory backed by PostgreSQL."""

    def __init__(
        self,
        connection_string: Optional[str] = None,
        *,
        host: Optional[str] = None,
        port: int = 5432,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        tenant_resolver: Optional[Callable[[User], str]] = None,
        share_within_tenant: bool = False,
        **kwargs: Any,
    ) -> None:
        try:
            import psycopg2
            import psycopg2.extras
        except Exception as e:  # pragma: no cover
            raise ImportError(
                "psycopg2 package is required. Install with: pip install 'vanna[postgres]'"
            ) from e

        self._psycopg2 = psycopg2
        self._extras = psycopg2.extras

        if connection_string:
            self._connection_string: Optional[str] = connection_string
            self._connection_params: Optional[dict] = None
        elif host and database and user:
            self._connection_string = None
            self._connection_params = {
                "host": host,
                "port": port,
                "dbname": database,
                "user": user,
                "password": password,
                **kwargs,
            }
        else:
            raise ValueError(
                "Provide either connection_string OR (host, database, user) parameters"
            )

        self._tenant_resolver: Callable[[User], str] = tenant_resolver or (
            lambda u: u.id
        )
        self._share_within_tenant = share_within_tenant
        self._init_schema()

    # ------------------------------------------------------------------ helpers

    def _connect(self):
        if self._connection_string:
            return self._psycopg2.connect(self._connection_string)
        return self._psycopg2.connect(**self._connection_params)

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

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

    # --------------------------------------------------------------- sync impls

    def _save_tool_sync(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        user: User,
        success: bool,
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vanna_tool_memories
                        (memory_id, user_id, tenant_id, question, tool_name,
                         args_json, timestamp, success, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        user.id,
                        self._tenant_of(user),
                        question,
                        tool_name,
                        json.dumps(args, ensure_ascii=False, default=str),
                        self._now_iso(),
                        success,
                        json.dumps(metadata or {}, ensure_ascii=False, default=str),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def _save_text_sync(self, content: str, user: User) -> TextMemory:
        memory = TextMemory(
            memory_id=str(uuid.uuid4()),
            content=content,
            timestamp=self._now_iso(),
            user_id=user.id,
            tenant_id=self._tenant_of(user),
        )
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vanna_text_memories
                        (memory_id, user_id, tenant_id, content, timestamp)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        memory.memory_id,
                        memory.user_id,
                        memory.tenant_id,
                        memory.content,
                        memory.timestamp,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return memory

    def _search_usage_sync(
        self,
        question: str,
        context: ToolContext,
        limit: int,
        similarity_threshold: float,
        tool_name_filter: Optional[str],
    ) -> List[ToolMemorySearchResult]:
        scope_col, scope_val = self._scope(context)
        query = (
            "SELECT * FROM vanna_tool_memories "
            f"WHERE {scope_col} = %s AND success = TRUE"
        )
        params: List[Any] = [scope_val]
        if tool_name_filter:
            query += " AND tool_name = %s"
            params.append(tool_name_filter)

        conn = self._connect()
        try:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        finally:
            conn.close()

        normalized = _normalize(question)
        scored: List[Tuple[ToolMemory, float]] = []
        for row in rows:
            memory = ToolMemory(
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
            score = min(_similarity(normalized, memory.question), 1.0)
            if score >= similarity_threshold:
                scored.append((memory, score))
        scored.sort(key=lambda item: item[1], reverse=True)

        return [
            ToolMemorySearchResult(memory=memory, similarity_score=score, rank=rank)
            for rank, (memory, score) in enumerate(scored[:limit], start=1)
        ]

    def _search_text_sync(
        self,
        query: str,
        context: ToolContext,
        limit: int,
        similarity_threshold: float,
    ) -> List[TextMemorySearchResult]:
        scope_col, scope_val = self._scope(context)
        conn = self._connect()
        try:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM vanna_text_memories WHERE {scope_col} = %s",
                    (scope_val,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        normalized = _normalize(query)
        scored: List[Tuple[TextMemory, float]] = []
        for row in rows:
            memory = TextMemory(
                memory_id=row["memory_id"],
                content=row["content"],
                timestamp=row["timestamp"],
                user_id=row["user_id"],
                tenant_id=row["tenant_id"],
            )
            score = min(_similarity(normalized, memory.content), 1.0)
            if score >= similarity_threshold:
                scored.append((memory, score))
        scored.sort(key=lambda item: item[1], reverse=True)

        return [
            TextMemorySearchResult(memory=memory, similarity_score=score, rank=rank)
            for rank, (memory, score) in enumerate(scored[:limit], start=1)
        ]

    def _recent_tool_sync(
        self, context: ToolContext, limit: int
    ) -> List[ToolMemory]:
        scope_col, scope_val = self._scope(context)
        conn = self._connect()
        try:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM vanna_tool_memories WHERE {scope_col} = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (scope_val, limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [
            ToolMemory(
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
            for row in rows
        ]

    def _recent_text_sync(
        self, context: ToolContext, limit: int
    ) -> List[TextMemory]:
        scope_col, scope_val = self._scope(context)
        conn = self._connect()
        try:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM vanna_text_memories WHERE {scope_col} = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (scope_val, limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [
            TextMemory(
                memory_id=row["memory_id"],
                content=row["content"],
                timestamp=row["timestamp"],
                user_id=row["user_id"],
                tenant_id=row["tenant_id"],
            )
            for row in rows
        ]

    def _delete_tool_sync(self, context: ToolContext, memory_id: str) -> bool:
        scope_col, scope_val = self._scope(context)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM vanna_tool_memories "
                    f"WHERE memory_id = %s AND {scope_col} = %s",
                    (memory_id, scope_val),
                )
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            conn.close()

    def _delete_text_sync(self, context: ToolContext, memory_id: str) -> bool:
        scope_col, scope_val = self._scope(context)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM vanna_text_memories "
                    f"WHERE memory_id = %s AND {scope_col} = %s",
                    (memory_id, scope_val),
                )
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            conn.close()

    def _clear_sync(
        self,
        context: ToolContext,
        tool_name: Optional[str],
        before_date: Optional[str],
    ) -> int:
        scope_col, scope_val = self._scope(context)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                tool_clauses = [f"{scope_col} = %s"]
                tool_params: List[Any] = [scope_val]
                if tool_name:
                    tool_clauses.append("tool_name = %s")
                    tool_params.append(tool_name)
                if before_date:
                    tool_clauses.append("(timestamp IS NULL OR timestamp < %s)")
                    tool_params.append(before_date)
                cur.execute(
                    f"DELETE FROM vanna_tool_memories "
                    f"WHERE {' AND '.join(tool_clauses)}",
                    tool_params,
                )
                deleted = cur.rowcount if cur.rowcount > 0 else 0

                if tool_name is None:
                    text_clauses = [f"{scope_col} = %s"]
                    text_params: List[Any] = [scope_val]
                    if before_date:
                        text_clauses.append("(timestamp IS NULL OR timestamp < %s)")
                        text_params.append(before_date)
                    cur.execute(
                        f"DELETE FROM vanna_text_memories "
                        f"WHERE {' AND '.join(text_clauses)}",
                        text_params,
                    )
                    deleted += cur.rowcount if cur.rowcount > 0 else 0
            conn.commit()
            return deleted
        finally:
            conn.close()

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
        await asyncio.to_thread(
            self._save_tool_sync,
            question,
            tool_name,
            args,
            context.user,
            success,
            metadata,
        )

    async def save_text_memory(self, content: str, context: ToolContext) -> TextMemory:
        return await asyncio.to_thread(self._save_text_sync, content, context.user)

    async def search_similar_usage(
        self,
        question: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        tool_name_filter: Optional[str] = None,
    ) -> List[ToolMemorySearchResult]:
        return await asyncio.to_thread(
            self._search_usage_sync,
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
        return await asyncio.to_thread(
            self._search_text_sync, query, context, limit, similarity_threshold
        )

    async def get_recent_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[ToolMemory]:
        return await asyncio.to_thread(self._recent_tool_sync, context, limit)

    async def get_recent_text_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[TextMemory]:
        return await asyncio.to_thread(self._recent_text_sync, context, limit)

    async def delete_by_id(self, context: ToolContext, memory_id: str) -> bool:
        return await asyncio.to_thread(self._delete_tool_sync, context, memory_id)

    async def delete_text_memory(
        self, context: ToolContext, memory_id: str
    ) -> bool:
        return await asyncio.to_thread(self._delete_text_sync, context, memory_id)

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        return await asyncio.to_thread(
            self._clear_sync, context, tool_name, before_date
        )
