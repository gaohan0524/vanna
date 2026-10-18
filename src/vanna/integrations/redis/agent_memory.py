"""
Redis-backed agent memory.

本模块提供 Redis 持久化智能体记忆（依赖 redis.asyncio）。数据模型：

- ``{prefix}:tool_mem:{id}`` / ``{prefix}:text_mem:{id}`` -> hash（记忆内容 + 归属）
- ``{prefix}:user:{user_id}:tool_mems`` -> zset（member=id，score=时间戳）
- ``{prefix}:user:{user_id}:text_mems`` -> zset
- 租户维度同理（``tenant:{tenant_id}:...``）。

默认按 user_id 隔离，``share_within_tenant=True`` 时按 tenant_id 隔离（组织内共享）。
检索使用与 DemoAgentMemory 一致的轻量相似度（Jaccard + difflib）。
"""

from __future__ import annotations

import difflib
import json
import time
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


class RedisAgentMemory(AgentMemory):
    """Persistent, multi-tenant agent memory backed by Redis."""

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        *,
        tenant_resolver: Optional[Callable[[User], str]] = None,
        share_within_tenant: bool = False,
        prefix: str = "vanna",
        **kwargs: Any,
    ) -> None:
        try:
            import redis.asyncio as aioredis
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "redis package is required. Install with: pip install 'vanna[redis]'"
            ) from e

        self._client = aioredis.Redis.from_url(
            url, decode_responses=True, **kwargs
        )
        self._prefix = prefix
        self._tenant_resolver: Callable[[User], str] = tenant_resolver or (
            lambda u: u.id
        )
        self._share_within_tenant = share_within_tenant

    # ------------------------------------------------------------------ helpers

    def _key(self, *parts: str) -> str:
        return ":".join((self._prefix, *parts))

    def _tenant_of(self, user: User) -> str:
        try:
            return str(self._tenant_resolver(user))
        except Exception:
            return user.id

    def _scope_prefix(self, context: ToolContext) -> str:
        if self._share_within_tenant:
            return ":".join(
                (self._prefix, "tenant", self._tenant_of(context.user))
            )
        return ":".join((self._prefix, "user", context.user.id))

    def _scope_field_and_value(self, context: ToolContext) -> Tuple[str, str]:
        if self._share_within_tenant:
            return "tenant_id", self._tenant_of(context.user)
        return "user_id", context.user.id

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()

    async def close(self) -> None:
        # redis-py 5.x 推荐 aclose()，4.x 用 close()；优先 aclose 以兼容两者
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()
        else:  # pragma: no cover - legacy redis-py
            await self._client.close()

    # ------------------------------------------------------------------ save

    async def save_tool_usage(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolContext,
        success: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        memory_id = str(uuid.uuid4())
        pipe = self._client.pipeline()
        pipe.hset(
            self._key("tool_mem", memory_id),
            mapping={
                "memory_id": memory_id,
                "user_id": context.user.id,
                "tenant_id": self._tenant_of(context.user),
                "question": question,
                "tool_name": tool_name,
                "args_json": json.dumps(args, ensure_ascii=False, default=str),
                "timestamp": self._now_iso(),
                "success": "1" if success else "0",
                "metadata_json": json.dumps(
                    metadata or {}, ensure_ascii=False, default=str
                ),
            },
        )
        pipe.zadd(
            f"{self._scope_prefix(context)}:tool_mems", {memory_id: time.time()}
        )
        await pipe.execute()

    async def save_text_memory(self, content: str, context: ToolContext) -> TextMemory:
        memory_id = str(uuid.uuid4())
        timestamp = self._now_iso()
        pipe = self._client.pipeline()
        pipe.hset(
            self._key("text_mem", memory_id),
            mapping={
                "memory_id": memory_id,
                "user_id": context.user.id,
                "tenant_id": self._tenant_of(context.user),
                "content": content,
                "timestamp": timestamp,
            },
        )
        pipe.zadd(
            f"{self._scope_prefix(context)}:text_mems", {memory_id: time.time()}
        )
        await pipe.execute()
        return TextMemory(
            memory_id=memory_id,
            content=content,
            timestamp=timestamp,
            user_id=context.user.id,
            tenant_id=self._tenant_of(context.user),
        )

    # ---------------------------------------------------------------- readers

    @staticmethod
    def _to_tool_memory(data: Dict[str, str]) -> ToolMemory:
        return ToolMemory(
            memory_id=data.get("memory_id"),
            question=data.get("question", ""),
            tool_name=data.get("tool_name", ""),
            args=json.loads(data.get("args_json") or "{}"),
            timestamp=data.get("timestamp"),
            success=data.get("success") == "1",
            metadata=json.loads(data.get("metadata_json") or "{}"),
            user_id=data.get("user_id"),
            tenant_id=data.get("tenant_id"),
        )

    @staticmethod
    def _to_text_memory(data: Dict[str, str]) -> TextMemory:
        return TextMemory(
            memory_id=data.get("memory_id"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp"),
            user_id=data.get("user_id"),
            tenant_id=data.get("tenant_id"),
        )

    async def _load_tool_memories(
        self, context: ToolContext
    ) -> List[ToolMemory]:
        ids = await self._client.zrange(
            f"{self._scope_prefix(context)}:tool_mems", 0, -1
        )
        if not ids:
            return []
        pipe = self._client.pipeline()
        for memory_id in ids:
            pipe.hgetall(self._key("tool_mem", memory_id))
        rows = await pipe.execute()
        return [self._to_tool_memory(row) for row in rows if row]

    async def _load_text_memories(
        self, context: ToolContext
    ) -> List[TextMemory]:
        ids = await self._client.zrange(
            f"{self._scope_prefix(context)}:text_mems", 0, -1
        )
        if not ids:
            return []
        pipe = self._client.pipeline()
        for memory_id in ids:
            pipe.hgetall(self._key("text_mem", memory_id))
        rows = await pipe.execute()
        return [self._to_text_memory(row) for row in rows if row]

    # --------------------------------------------------------------- searches

    async def search_similar_usage(
        self,
        question: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        tool_name_filter: Optional[str] = None,
    ) -> List[ToolMemorySearchResult]:
        normalized = _normalize(question)
        memories = await self._load_tool_memories(context)

        scored: List[Tuple[ToolMemory, float]] = []
        for memory in memories:
            if not memory.success:
                continue
            if tool_name_filter and memory.tool_name != tool_name_filter:
                continue
            score = min(_similarity(normalized, memory.question), 1.0)
            if score >= similarity_threshold:
                scored.append((memory, score))
        scored.sort(key=lambda item: item[1], reverse=True)

        return [
            ToolMemorySearchResult(memory=memory, similarity_score=score, rank=rank)
            for rank, (memory, score) in enumerate(scored[:limit], start=1)
        ]

    async def search_text_memories(
        self,
        query: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> List[TextMemorySearchResult]:
        normalized = _normalize(query)
        memories = await self._load_text_memories(context)

        scored: List[Tuple[TextMemory, float]] = []
        for memory in memories:
            score = min(_similarity(normalized, memory.content), 1.0)
            if score >= similarity_threshold:
                scored.append((memory, score))
        scored.sort(key=lambda item: item[1], reverse=True)

        return [
            TextMemorySearchResult(memory=memory, similarity_score=score, rank=rank)
            for rank, (memory, score) in enumerate(scored[:limit], start=1)
        ]

    async def get_recent_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[ToolMemory]:
        ids = await self._client.zrange(
            f"{self._scope_prefix(context)}:tool_mems", 0, limit - 1, desc=True
        )
        if not ids:
            return []
        pipe = self._client.pipeline()
        for memory_id in ids:
            pipe.hgetall(self._key("tool_mem", memory_id))
        rows = await pipe.execute()
        return [self._to_tool_memory(row) for row in rows if row]

    async def get_recent_text_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[TextMemory]:
        ids = await self._client.zrange(
            f"{self._scope_prefix(context)}:text_mems", 0, limit - 1, desc=True
        )
        if not ids:
            return []
        pipe = self._client.pipeline()
        for memory_id in ids:
            pipe.hgetall(self._key("text_mem", memory_id))
        rows = await pipe.execute()
        return [self._to_text_memory(row) for row in rows if row]

    # ---------------------------------------------------------------- deletes

    async def delete_by_id(self, context: ToolContext, memory_id: str) -> bool:
        field, value = self._scope_field_and_value(context)
        data = await self._client.hgetall(self._key("tool_mem", memory_id))
        if not data or data.get(field) != value:
            return False
        pipe = self._client.pipeline()
        pipe.delete(self._key("tool_mem", memory_id))
        pipe.zrem(f"{self._scope_prefix(context)}:tool_mems", memory_id)
        await pipe.execute()
        return True

    async def delete_text_memory(
        self, context: ToolContext, memory_id: str
    ) -> bool:
        field, value = self._scope_field_and_value(context)
        data = await self._client.hgetall(self._key("text_mem", memory_id))
        if not data or data.get(field) != value:
            return False
        pipe = self._client.pipeline()
        pipe.delete(self._key("text_mem", memory_id))
        pipe.zrem(f"{self._scope_prefix(context)}:text_mems", memory_id)
        await pipe.execute()
        return True

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        deleted = 0

        tool_memories = await self._load_tool_memories(context)
        for memory in tool_memories:
            if tool_name and memory.tool_name != tool_name:
                continue
            if before_date and memory.timestamp and memory.timestamp >= before_date:
                continue
            pipe = self._client.pipeline()
            pipe.delete(self._key("tool_mem", memory.memory_id))
            pipe.zrem(
                f"{self._scope_prefix(context)}:tool_mems", memory.memory_id
            )
            await pipe.execute()
            deleted += 1

        # 按工具名清理时不动文本记忆（与 DemoAgentMemory 行为一致）
        if tool_name is None:
            text_memories = await self._load_text_memories(context)
            for memory in text_memories:
                if (
                    before_date
                    and memory.timestamp
                    and memory.timestamp >= before_date
                ):
                    continue
                pipe = self._client.pipeline()
                pipe.delete(self._key("text_mem", memory.memory_id))
                pipe.zrem(
                    f"{self._scope_prefix(context)}:text_mems", memory.memory_id
                )
                await pipe.execute()
                deleted += 1

        return deleted
