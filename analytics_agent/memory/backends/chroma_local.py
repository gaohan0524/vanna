"""Local Chroma-backed AgentMemory with app-level tenancy and deterministic IDs.

This is intentionally an application-side adapter, not a change to
``vanna.integrations.chromadb``.  The framework integration remains generic;
this class adds the rules the analytics agent needs:

- shared / team / user visibility
- deterministic IDs for idempotent seeding
- flattened metadata fields that Chroma can filter on
- a local ``PersistentClient`` directory

存储约定（排查时先看这里）：

- ``kind=tool``：工具记忆，document 是 question，``args_json`` 是 SQL 参数；
- ``kind=text``：文本知识，document 是知识正文；
- ``scope``：``shared`` / ``team`` / ``user``；
- ``owner_id``：shared 固定为 ``shared``，team 为 tenant，user 为 user id；
- 复杂 dict 一律序列化成 JSON 字符串，因为 Chroma metadata 只接受标量。

所有同步 Chroma 调用都通过 ``asyncio.to_thread`` 放到线程池，避免阻塞
Agent 的异步事件循环。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import chromadb
    from chromadb.config import Settings
    from chromadb.errors import NotFoundError
    from chromadb.utils import embedding_functions
except ImportError as exc:  # pragma: no cover - exercised only without optional dep
    raise ImportError(
        "chromadb is required for ChromaLocalMemory. Install with: pip install chromadb"
    ) from exc

from vanna.capabilities.agent_memory import (
    AgentMemory,
    TextMemory,
    TextMemorySearchResult,
    ToolMemory,
    ToolMemorySearchResult,
)
from vanna.core.tool import ToolContext


def _sha256(value: str) -> str:
    """Return a stable hex digest used as the Chroma document id."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    """Serialize nested metadata deterministically for Chroma scalar fields."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _embedding_name(embedding_function: Any) -> str:
    """Read the model identity used to bind a collection to an encoder.

    Chroma persists vectors, not the model name.  Writing this string into
    collection metadata lets us fail loudly when ``MEMORY_EMBEDDING_MODEL`` is
    changed without rebuilding the collection.
    """
    name = getattr(embedding_function, "name", None)
    if callable(name):
        name = name()
    return str(name or embedding_function.__class__.__name__)


class ChromaLocalMemory(AgentMemory):
    """Chroma PersistentClient memory scoped by user/team/shared visibility.

    一个 collection 只绑定一个 embedding 模型；模型名写入 collection metadata，
    启动时校验。这样不会出现“旧向量用新模型查询”的静默错误。
    """

    def __init__(
        self,
        persist_directory: str,
        collection_name: str = "analytics_memory",
        embedding_function: Optional[Any] = None,
    ) -> None:
        """Open or create the local collection.

        Args:
            persist_directory: Local Chroma directory (git-ignored in this repo).
            collection_name: Logical collection name; changing it gives a clean
                slate without deleting the old vectors.
            embedding_function: Chroma-compatible encoder.  Production callers
                should pass ``ChineseEmbeddingFunction``; the default is kept
                only for smoke tests and low-quality local experiments.
        """
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self._embedding_function = (
            embedding_function or embedding_functions.DefaultEmbeddingFunction()
        )
        self._embedding_model = _embedding_name(self._embedding_function)
        self._client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._open_or_create_collection()

    def _open_or_create_collection(self):
        """Open a collection only if its embedding model matches this config.

        A missing collection is created with:
        - cosine distance;
        - the encoder identity in ``embedding_model``.

        An existing collection without that metadata is rejected because we
        cannot prove which encoder produced its vectors.
        """
        try:
            collection = self._client.get_collection(
                name=self.collection_name,
                embedding_function=self._embedding_function,
            )
        except NotFoundError:
            # New collection: cosine is appropriate for normalized embeddings,
            # and embedding_model becomes the compatibility contract.
            return self._client.create_collection(
                name=self.collection_name,
                embedding_function=self._embedding_function,
                metadata={
                    "hnsw:space": "cosine",
                    "embedding_model": self._embedding_model,
                },
            )

        stored_model = (collection.metadata or {}).get("embedding_model")
        if stored_model is None:
            raise ValueError(
                f"Chroma collection {self.collection_name!r} has no "
                "'embedding_model' metadata. It may have been created by an older "
                "version. Create a new MEMORY_COLLECTION or delete and rebuild it "
                f"with {self._embedding_model!r}."
            )
        if str(stored_model) != self._embedding_model:
            raise ValueError(
                f"Embedding model mismatch for Chroma collection "
                f"{self.collection_name!r}: stored={stored_model!r}, "
                f"configured={self._embedding_model!r}. Use a new "
                "MEMORY_COLLECTION or rebuild this collection and re-import seeds."
            )
        return collection

    @property
    def collection(self):
        """Expose the underlying collection for health checks/tests."""
        return self._collection

    # ------------------------------------------------------------------ access
    @staticmethod
    def _owner(context: ToolContext) -> tuple[str, Optional[str], Optional[str]]:
        """Resolve user/tenant from ToolContext.

        ``tenant_id`` is read from ``user.metadata`` first, then from an optional
        custom attribute because ``User`` allows extra fields.  The third tuple
        item is kept for readability at call sites (it is always the user id).
        """
        user_id = context.user.id
        tenant_id = context.user.metadata.get("tenant_id") or getattr(
            context.user, "tenant_id", None
        )
        return user_id, str(tenant_id) if tenant_id else None, user_id

    @staticmethod
    def _scope(
        metadata: Optional[Dict[str, Any]], context: ToolContext
    ) -> tuple[str, str]:
        """Normalize visibility metadata to ``(scope, owner_id)``.

        Default is ``user`` (private).  Seed import must explicitly pass
        ``{"scope": "shared"}``; a team scope falls back to the user id when no
        tenant is configured, so it never becomes globally visible.
        """
        requested = str((metadata or {}).get("scope") or "user")
        user_id, tenant_id, _ = ChromaLocalMemory._owner(context)
        if requested == "shared":
            return "shared", "shared"
        if requested == "team":
            return "team", tenant_id or user_id
        return "user", user_id

    def _access_filter(self, context: ToolContext) -> Dict[str, Any]:
        """Build Chroma's visibility predicate: shared OR my team OR my user."""
        user_id, tenant_id, _ = self._owner(context)
        conditions: List[Dict[str, Any]] = [{"scope": "shared"}]
        if tenant_id:
            conditions.append({"$and": [{"scope": "team"}, {"owner_id": tenant_id}]})
        conditions.append({"$and": [{"scope": "user"}, {"owner_id": user_id}]})
        return conditions[0] if len(conditions) == 1 else {"$or": conditions}

    def _where(
        self, context: ToolContext, *conditions: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Combine the visibility predicate with kind/success/tool filters."""
        return {"$and": [self._access_filter(context), *conditions]}

    @staticmethod
    def _can_access(metadata: Dict[str, Any], context: ToolContext) -> bool:
        """Re-check ownership before destructive operations.

        Chroma filters reads, but delete-by-id still needs an explicit check to
        prevent a user from deleting another user's private memory by id.
        """
        scope = metadata.get("scope")
        if scope == "shared":
            return True
        user_id, tenant_id, _ = ChromaLocalMemory._owner(context)
        if scope == "team":
            return bool(tenant_id) and metadata.get("owner_id") == tenant_id
        return metadata.get("owner_id") == user_id

    # ------------------------------------------------------------------- save
    def _tool_memory_id(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolContext,
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        """Build a deterministic tool-memory id.

        Idempotency key is ``(question, tool, sql_hash|args, scope, owner)``.
        Using ``sql_hash`` when provided makes seed re-imports stable even if
        argument key ordering changes.
        """
        scope, owner = self._scope(metadata, context)
        sql_hash = (metadata or {}).get("sql_hash")
        payload = sql_hash or _json(args)
        return "tool:" + _sha256(
            _json(
                {
                    "question": question,
                    "tool_name": tool_name,
                    "payload": payload,
                    "scope": scope,
                    "owner": owner,
                }
            )
        )

    async def save_tool_usage(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolContext,
        success: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Upsert one question → tool/SQL pattern.

        ``args`` and caller metadata are JSON-encoded because Chroma metadata
        cannot store nested dicts.  The document embedded for similarity is the
        natural-language question, not the SQL body.
        """
        scope, owner = self._scope(metadata, context)
        user_id, tenant_id, _ = self._owner(context)
        memory_id = self._tool_memory_id(question, tool_name, args, context, metadata)
        timestamp = datetime.now().isoformat()
        # Chroma metadata must stay flat: nested args/metadata are JSON strings.
        meta = {
            "kind": "tool",
            "question": question,
            "tool_name": tool_name,
            "args_json": _json(args),
            "metadata_json": _json(metadata or {}),
            "timestamp": timestamp,
            "success": bool(success),
            "scope": scope,
            "owner_id": owner,
            "user_id": user_id,
            "tenant_id": tenant_id or "",
            "is_text_memory": False,
        }
        await asyncio.to_thread(
            self._collection.upsert,
            ids=[memory_id],
            documents=[question],
            metadatas=[meta],
        )

    async def save_text_memory(
        self,
        content: str,
        context: ToolContext,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TextMemory:
        """Upsert one free-form knowledge item.

        The optional metadata argument is an app-side extension used by seed
        import (source/version/tags).  It is stored as JSON, while scope and
        owner are also flattened for filtering.
        """
        scope, owner = self._scope(metadata, context)
        user_id, tenant_id, _ = self._owner(context)
        memory_id = "text:" + _sha256(
            _json({"content": content, "scope": scope, "owner": owner})
        )
        timestamp = datetime.now().isoformat()
        meta = {
            "kind": "text",
            "content": content,
            "timestamp": timestamp,
            "scope": scope,
            "owner_id": owner,
            "user_id": user_id,
            "tenant_id": tenant_id or "",
            "is_text_memory": True,
            "metadata_json": _json(metadata or {}),
        }
        await asyncio.to_thread(
            self._collection.upsert,
            ids=[memory_id],
            documents=[content],
            metadatas=[meta],
        )
        return TextMemory(
            memory_id=memory_id,
            content=content,
            timestamp=timestamp,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    # ----------------------------------------------------------------- search
    @staticmethod
    def _score(distance: Optional[float]) -> float:
        """Convert Chroma cosine distance to a [0,1] similarity score."""
        if distance is None:
            return 0.0
        return max(0.0, min(1.0, 1.0 - float(distance)))

    async def search_similar_usage(
        self,
        question: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        tool_name_filter: Optional[str] = None,
    ) -> List[ToolMemorySearchResult]:
        """Search successful tool patterns visible to the current user.

        Filters are applied inside Chroma (kind/success/tool/visibility), then
        the caller's similarity threshold is applied to the returned score.
        """
        conditions: List[Dict[str, Any]] = [{"kind": "tool"}, {"success": True}]
        if tool_name_filter:
            conditions.append({"tool_name": tool_name_filter})
        where = self._where(context, *conditions)

        def _search():
            return self._collection.query(
                query_texts=[question],
                n_results=max(1, limit),
                where=where,
            )

        results = await asyncio.to_thread(_search)
        output: List[ToolMemorySearchResult] = []
        # Chroma returns one nested list per query; this call sends one query.
        ids = results.get("ids") or [[]]
        metadatas = results.get("metadatas") or [[]]
        distances = results.get("distances") or [[]]
        for index, (memory_id, metadata, distance) in enumerate(
            zip(ids[0], metadatas[0], distances[0]), start=1
        ):
            score = self._score(distance)
            if score < similarity_threshold:
                continue
            output.append(
                ToolMemorySearchResult(
                    memory=ToolMemory(
                        memory_id=memory_id,
                        question=metadata.get("question", ""),
                        tool_name=metadata.get("tool_name", ""),
                        args=json.loads(metadata.get("args_json") or "{}"),
                        timestamp=metadata.get("timestamp"),
                        success=bool(metadata.get("success", True)),
                        metadata=json.loads(metadata.get("metadata_json") or "{}"),
                        user_id=metadata.get("user_id") or None,
                        tenant_id=metadata.get("tenant_id") or None,
                    ),
                    similarity_score=score,
                    rank=index,
                )
            )
        return output

    async def search_text_memories(
        self,
        query: str,
        context: ToolContext,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> List[TextMemorySearchResult]:
        """Search text knowledge visible to the current user."""
        where = self._where(context, {"kind": "text"})

        def _search():
            return self._collection.query(
                query_texts=[query],
                n_results=max(1, limit),
                where=where,
            )

        results = await asyncio.to_thread(_search)
        output: List[TextMemorySearchResult] = []
        # Keep parsing symmetric with search_similar_usage: one query in.
        ids = results.get("ids") or [[]]
        metadatas = results.get("metadatas") or [[]]
        distances = results.get("distances") or [[]]
        for index, (memory_id, metadata, distance) in enumerate(
            zip(ids[0], metadatas[0], distances[0]), start=1
        ):
            score = self._score(distance)
            if score < similarity_threshold:
                continue
            output.append(
                TextMemorySearchResult(
                    memory=TextMemory(
                        memory_id=memory_id,
                        content=metadata.get("content", ""),
                        timestamp=metadata.get("timestamp"),
                        user_id=metadata.get("user_id") or None,
                        tenant_id=metadata.get("tenant_id") or None,
                    ),
                    similarity_score=score,
                    rank=index,
                )
            )
        return output

    # ------------------------------------------------------------------ recent
    async def get_recent_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[ToolMemory]:
        """Return visible tool memories ordered by timestamp descending."""
        where = self._where(context, {"kind": "tool"})

        def _get():
            return self._collection.get(where=where, include=["metadatas"])

        results = await asyncio.to_thread(_get)
        rows = list(zip(results.get("ids") or [], results.get("metadatas") or []))
        rows.sort(key=lambda row: row[1].get("timestamp", ""), reverse=True)
        output: List[ToolMemory] = []
        for memory_id, metadata in rows[:limit]:
            output.append(
                ToolMemory(
                    memory_id=memory_id,
                    question=metadata.get("question", ""),
                    tool_name=metadata.get("tool_name", ""),
                    args=json.loads(metadata.get("args_json") or "{}"),
                    timestamp=metadata.get("timestamp"),
                    success=bool(metadata.get("success", True)),
                    metadata=json.loads(metadata.get("metadata_json") or "{}"),
                    user_id=metadata.get("user_id") or None,
                    tenant_id=metadata.get("tenant_id") or None,
                )
            )
        return output

    async def get_recent_text_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[TextMemory]:
        """Return visible text memories ordered by timestamp descending."""
        where = self._where(context, {"kind": "text"})

        def _get():
            return self._collection.get(where=where, include=["metadatas"])

        results = await asyncio.to_thread(_get)
        rows = list(zip(results.get("ids") or [], results.get("metadatas") or []))
        rows.sort(key=lambda row: row[1].get("timestamp", ""), reverse=True)
        return [
            TextMemory(
                memory_id=memory_id,
                content=metadata.get("content", ""),
                timestamp=metadata.get("timestamp"),
                user_id=metadata.get("user_id") or None,
                tenant_id=metadata.get("tenant_id") or None,
            )
            for memory_id, metadata in rows[:limit]
        ]

    # ------------------------------------------------------------------ delete
    async def delete_by_id(self, context: ToolContext, memory_id: str) -> bool:
        """Delete a memory only when the caller is allowed to see it."""

        def _delete():
            rows = self._collection.get(ids=[memory_id], include=["metadatas"])
            metadatas = rows.get("metadatas") or []
            if not metadatas or not self._can_access(metadatas[0], context):
                return False
            self._collection.delete(ids=[memory_id])
            return True

        return await asyncio.to_thread(_delete)

    async def delete_text_memory(self, context: ToolContext, memory_id: str) -> bool:
        """Text memories share the same ownership check as tool memories."""
        return await self.delete_by_id(context, memory_id)

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        """Delete visible tool/text memories, optionally filtered.

        ``tool_name`` only applies to tool memories; text memories are skipped
        when a tool filter is supplied.  Date comparison uses ISO timestamps.
        """
        where = self._access_filter(context)

        def _clear():
            rows = self._collection.get(where=where, include=["metadatas"])
            ids: List[str] = []
            for memory_id, metadata in zip(
                rows.get("ids") or [], rows.get("metadatas") or []
            ):
                if metadata.get("kind") == "tool" and tool_name:
                    if metadata.get("tool_name") != tool_name:
                        continue
                if metadata.get("kind") == "text" and tool_name:
                    continue
                if before_date and metadata.get("timestamp", "") >= before_date:
                    continue
                ids.append(memory_id)
            if ids:
                self._collection.delete(ids=ids)
            return len(ids)

        return await asyncio.to_thread(_clear)
