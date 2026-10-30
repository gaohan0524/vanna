"""基于本地 Chroma 的 AgentMemory，带应用级租户隔离和确定性 ID。

这是应用侧适配器，不修改 ``vanna.integrations.chromadb`` 的通用实现；
框架集成保持通用，本类只补充分析 Agent 需要的规则：

- shared / team / user 三种可见性；
- 确定性 ID，支持种子数据幂等导入；
- 扁平 metadata，便于 Chroma 过滤；
- 本地 ``PersistentClient`` 目录。

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
    """返回稳定的十六进制摘要，用作 Chroma 文档 ID。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    """把嵌套 metadata 稳定序列化，供 Chroma 存放标量字段。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _flatten_seed_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把种子导入需要的标量字段提升到顶层，供 Chroma where/get 过滤。"""
    if not metadata:
        return {}
    result: Dict[str, Any] = {}
    for key in (
        "seed_id",
        "seed_source",
        "seed_version",
        "seed_hash",
        "seed_kind",
        "reviewed",
    ):
        value = metadata.get(key)
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
    return result


def _embedding_name(embedding_function: Any) -> str:
    """读取用于绑定 collection 的模型标识。

    Chroma 只保存向量，不自动记录模型名。把标识写入 collection metadata，
    才能在 ``MEMORY_EMBEDDING_MODEL`` 被改动却没有重建 collection 时显式报错。
    """
    name = getattr(embedding_function, "name", None)
    if callable(name):
        name = name()
    return str(name or embedding_function.__class__.__name__)


class ChromaLocalMemory(AgentMemory):
    """按 user/team/shared 隔离的 Chroma PersistentClient 记忆实现。

    一个 collection 只绑定一个 embedding 模型；模型名写入 collection metadata，
    启动时校验。这样不会出现“旧向量用新模型查询”的静默错误。
    """

    def __init__(
        self,
        persist_directory: str,
        collection_name: str = "analytics_memory",
        embedding_function: Optional[Any] = None,
    ) -> None:
        """打开或创建本地 collection。

        参数：
            persist_directory: 本地 Chroma 目录；本仓库已加入 .gitignore。
            collection_name: collection 逻辑名；换名可得到干净的新库，
                不需要删除旧向量。
            embedding_function: Chroma 兼容编码器。生产调用方应传
                ``ChineseEmbeddingFunction``；默认值只用于 smoke test
                和低质量本地实验。
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
        """只有 embedding 模型匹配时才打开已有 collection。

        新建 collection 时写入：

        - cosine 距离；
        - 编码器标识 ``embedding_model``。

        已存在的 collection 如果没有该 metadata，直接拒绝：无法证明旧向量
        是哪个模型生成的，不能冒险混用。
        """
        try:
            collection = self._client.get_collection(
                name=self.collection_name,
                embedding_function=self._embedding_function,
            )
        except NotFoundError:
            # 新建 collection：归一化向量适合 cosine；同时把模型标识写成
            # 兼容性契约，后续打开时按它校验。
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
        """暴露底层 collection，供健康检查和测试使用。"""
        return self._collection

    # ------------------------------------------------------------------ 可见性
    @staticmethod
    def _owner(context: ToolContext) -> tuple[str, Optional[str], Optional[str]]:
        """从 ToolContext 解析用户和租户。

        ``tenant_id`` 先读 ``user.metadata``，再读可选的自定义属性，
        因为 ``User`` 允许扩展字段。第三个返回值保留给调用方提高可读性，
        它始终等于 user id。
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
        """把可见性 metadata 归一化为 ``(scope, owner_id)``。

        默认 ``user``（私有）。种子导入必须显式传
        ``{"scope": "shared"}``；未配置 tenant 时 team 会回退到 user id，
        不会意外变成全局可见。
        """
        requested = str((metadata or {}).get("scope") or "user")
        user_id, tenant_id, _ = ChromaLocalMemory._owner(context)
        if requested == "shared":
            return "shared", "shared"
        if requested == "team":
            return "team", tenant_id or user_id
        return "user", user_id

    def _access_filter(self, context: ToolContext) -> Dict[str, Any]:
        """构造 Chroma 可见性条件：shared 或本团队或本用户。"""
        user_id, tenant_id, _ = self._owner(context)
        conditions: List[Dict[str, Any]] = [{"scope": "shared"}]
        if tenant_id:
            conditions.append({"$and": [{"scope": "team"}, {"owner_id": tenant_id}]})
        conditions.append({"$and": [{"scope": "user"}, {"owner_id": user_id}]})
        return conditions[0] if len(conditions) == 1 else {"$or": conditions}

    def _where(
        self, context: ToolContext, *conditions: Dict[str, Any]
    ) -> Dict[str, Any]:
        """把可见性条件与 kind/success/tool 等业务条件组合起来。"""
        return {"$and": [self._access_filter(context), *conditions]}

    @staticmethod
    def _can_access(metadata: Dict[str, Any], context: ToolContext) -> bool:
        """删除前重新检查所有权。

        Chroma 只在读查询里过滤；按 ID 删除仍需显式校验，防止用户通过 ID
        删除别人的私有记忆。
        """
        scope = metadata.get("scope")
        if scope == "shared":
            return True
        user_id, tenant_id, _ = ChromaLocalMemory._owner(context)
        if scope == "team":
            return bool(tenant_id) and metadata.get("owner_id") == tenant_id
        return metadata.get("owner_id") == user_id

    # ------------------------------------------------------------------- 写入
    def _tool_memory_id(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolContext,
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        """生成确定性的工具记忆 ID。

        幂等键为 ``(question, tool, sql_hash|args, scope, owner)``。
        如果调用方提供 ``sql_hash``，优先使用它，这样参数 key 顺序变化
        也不会导致种子重复导入。
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

    def tool_memory_id(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolContext,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """公开的确定性工具 ID，供导入器的 dry-run/幂等比对复用。"""
        return self._tool_memory_id(question, tool_name, args, context, metadata)

    async def save_tool_usage(
        self,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolContext,
        success: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """写入一条“问题 → 工具/SQL”模式。

        ``args`` 和调用方 metadata 都序列化为 JSON，因为 Chroma metadata
        不能存嵌套 dict。用于相似度召回的是自然语言问题，不是 SQL 正文。
        """
        scope, owner = self._scope(metadata, context)
        user_id, tenant_id, _ = self._owner(context)
        memory_id = self._tool_memory_id(question, tool_name, args, context, metadata)
        timestamp = datetime.now().isoformat()
        # Chroma metadata 必须保持扁平：args 和 metadata 都转成 JSON 字符串。
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
            **_flatten_seed_metadata(metadata),
        }
        await asyncio.to_thread(
            self._collection.upsert,
            ids=[memory_id],
            documents=[question],
            metadatas=[meta],
        )

    def text_memory_id(
        self,
        content: str,
        context: ToolContext,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """公开的确定性文本 ID，供导入器的 dry-run/幂等比对复用。"""
        scope, owner = self._scope(metadata, context)
        return "text:" + _sha256(
            _json({"content": content, "scope": scope, "owner": owner})
        )

    async def save_text_memory(
        self,
        content: str,
        context: ToolContext,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TextMemory:
        """写入一条自由文本知识。

        可选 metadata 是应用侧扩展，供种子导入使用（source/version/tags）。
        它整体存为 JSON；scope 和 owner 另外扁平化，便于过滤。
        """
        scope, owner = self._scope(metadata, context)
        user_id, tenant_id, _ = self._owner(context)
        memory_id = self.text_memory_id(content, context, metadata)
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
            **_flatten_seed_metadata(metadata),
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

    # ----------------------------------------------------------------- 检索
    @staticmethod
    def _score(distance: Optional[float]) -> float:
        """把 Chroma 的 cosine 距离转换为 [0,1] 的相似度分数。"""
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
        """检索当前用户可见的成功工具模式。

        可见性、kind、success、tool_name 都在 Chroma 内部过滤；
        返回后再按调用方传入的相似度阈值裁剪。
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
        # Chroma 每个 query 返回一层嵌套列表；这里只发一个 query。
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
        """检索当前用户可见的文本知识。"""
        where = self._where(context, {"kind": "text"})

        def _search():
            return self._collection.query(
                query_texts=[query],
                n_results=max(1, limit),
                where=where,
            )

        results = await asyncio.to_thread(_search)
        output: List[TextMemorySearchResult] = []
        # 与 search_similar_usage 保持一致：只发一个 query，解析第一层结果。
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

    # ------------------------------------------------------------------ 最近记忆
    async def list_memories(
        self, context: ToolContext, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """列出当前用户可见的记忆，返回可读文档和 metadata。

        这是给管理/调试页面用的只读接口，不返回 embedding 向量；
        返回的 ``content`` / ``question`` 就是 Chroma 中保存的 document。
        """
        where = self._access_filter(context)

        def _list():
            rows = self._collection.get(
                where=where,
                include=["documents", "metadatas"],
                limit=limit,
            )
            output: List[Dict[str, Any]] = []
            for memory_id, document, metadata in zip(
                rows.get("ids") or [],
                rows.get("documents") or [],
                rows.get("metadatas") or [],
            ):
                if not metadata:
                    continue
                meta: Dict[str, Any] = {}
                raw_meta = metadata.get("metadata_json")
                if raw_meta:
                    try:
                        meta = json.loads(raw_meta)
                    except (TypeError, ValueError):
                        meta = {}
                kind = metadata.get("kind")
                args: Dict[str, Any] = {}
                if kind == "tool":
                    try:
                        args = json.loads(metadata.get("args_json") or "{}")
                    except (TypeError, ValueError):
                        args = {}
                output.append(
                    {
                        "memory_id": memory_id,
                        "kind": kind,
                        "document": document or "",
                        "content": document if kind == "text" else "",
                        "question": document if kind == "tool" else "",
                        "sql": args.get("sql") if kind == "tool" else "",
                        "source": meta.get("source") or metadata.get("seed_source", ""),
                        "version": meta.get("version")
                        or metadata.get("seed_version", 1),
                        "tags": meta.get("tags", []),
                        "scope": metadata.get("scope", "user"),
                        "owner_id": metadata.get("owner_id", ""),
                        "reviewed": bool(metadata.get("reviewed", False)),
                        "timestamp": metadata.get("timestamp", ""),
                    }
                )
            return sorted(output, key=lambda item: item["timestamp"], reverse=True)

        return await asyncio.to_thread(_list)

    async def get_recent_memories(
        self, context: ToolContext, limit: int = 10
    ) -> List[ToolMemory]:
        """返回当前用户可见的工具记忆，按时间倒序。"""
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
        """返回当前用户可见的文本记忆，按时间倒序。"""
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

    # ------------------------------------------------------------------ 删除
    async def delete_by_id(self, context: ToolContext, memory_id: str) -> bool:
        """只有调用方有权访问该记忆时才删除。"""

        def _delete():
            rows = self._collection.get(ids=[memory_id], include=["metadatas"])
            metadatas = rows.get("metadatas") or []
            if not metadatas or not self._can_access(metadatas[0], context):
                return False
            self._collection.delete(ids=[memory_id])
            return True

        return await asyncio.to_thread(_delete)

    async def delete_text_memory(self, context: ToolContext, memory_id: str) -> bool:
        """文本记忆复用工具记忆的所有权检查。"""
        return await self.delete_by_id(context, memory_id)

    async def clear_memories(
        self,
        context: ToolContext,
        tool_name: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> int:
        """删除当前用户可见的工具/文本记忆，可按条件过滤。

        ``tool_name`` 只作用于工具记忆；指定工具名时文本记忆会被跳过。
        日期比较使用 ISO 时间戳字符串。
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
