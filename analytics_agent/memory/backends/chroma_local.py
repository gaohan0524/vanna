"""Local Chroma-backed AgentMemory with app-level tenancy and deterministic IDs.

This is intentionally an application-side adapter, not a change to
``vanna.integrations.chromadb``.  The framework integration remains generic;
this class adds the rules the analytics agent needs:

- shared / team / user visibility
- deterministic IDs for idempotent seeding
- flattened metadata fields that Chroma can filter on
- a local ``PersistentClient`` directory
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
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _embedding_name(embedding_function: Any) -> str:
    name = getattr(embedding_function, "name", None)
    if callable(name):
        name = name()
    return str(name or embedding_function.__class__.__name__)


class ChromaLocalMemory(AgentMemory):
    """Chroma PersistentClient memory scoped by user/team/shared visibility."""

    def __init__(
        self,
        persist_directory: str,
        collection_name: str = "analytics_memory",
        embedding_function: Optional[Any] = None,
    ) -> None:
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
        """Open a collection only if its embedding model matches this config."""
        try:
            collection = self._client.get_collection(
                name=self.collection_name,
                embedding_function=self._embedding_function,
            )
        except NotFoundError:
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
        user_id = context.user.id
        tenant_id = context.user.metadata.get("tenant_id") or getattr(
            context.user, "tenant_id", None
        )
        return user_id, str(tenant_id) if tenant_id else None, user_id

    @staticmethod
    def _scope(
        metadata: Optional[Dict[str, Any]], context: ToolContext
    ) -> tuple[str, str]:
        requested = str((metadata or {}).get("scope") or "user")
        user_id, tenant_id, _ = ChromaLocalMemory._owner(context)
        if requested == "shared":
            return "shared", "shared"
        if requested == "team":
            return "team", tenant_id or user_id
        return "user", user_id

    def _access_filter(self, context: ToolContext) -> Dict[str, Any]:
        user_id, tenant_id, _ = self._owner(context)
        conditions: List[Dict[str, Any]] = [{"scope": "shared"}]
        if tenant_id:
            conditions.append({"$and": [{"scope": "team"}, {"owner_id": tenant_id}]})
        conditions.append({"$and": [{"scope": "user"}, {"owner_id": user_id}]})
        return conditions[0] if len(conditions) == 1 else {"$or": conditions}

    def _where(
        self, context: ToolContext, *conditions: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {"$and": [self._access_filter(context), *conditions]}

    @staticmethod
    def _can_access(metadata: Dict[str, Any], context: ToolContext) -> bool:
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
        scope, owner = self._scope(metadata, context)
        user_id, tenant_id, _ = self._owner(context)
        memory_id = self._tool_memory_id(question, tool_name, args, context, metadata)
        timestamp = datetime.now().isoformat()
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
        where = self._where(context, {"kind": "text"})

        def _search():
            return self._collection.query(
                query_texts=[query],
                n_results=max(1, limit),
                where=where,
            )

        results = await asyncio.to_thread(_search)
        output: List[TextMemorySearchResult] = []
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
        def _delete():
            rows = self._collection.get(ids=[memory_id], include=["metadatas"])
            metadatas = rows.get("metadatas") or []
            if not metadatas or not self._can_access(metadatas[0], context):
                return False
            self._collection.delete(ids=[memory_id])
            return True

        return await asyncio.to_thread(_delete)

    async def delete_text_memory(self, context: ToolContext, memory_id: str) -> bool:
        return await self.delete_by_id(context, memory_id)

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
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
