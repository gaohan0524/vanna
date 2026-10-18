"""
Redis-backed conversation store.

本模块提供 Redis 持久化会话存储（依赖 ``vanna[redis]`` 的 redis.asyncio）。数据模型：

- ``{prefix}:conv:{id}``                 -> hash（会话元数据 + 归属用户/租户）
- ``{prefix}:conv:{id}:messages``        -> list（按顺序存放 message JSON，追加写）
- ``{prefix}:user:{user_id}:convs``      -> zset（member=会话 id，score=updated_at 时间戳）
- ``{prefix}:tenant:{tenant_id}:convs``  -> zset（租户内共享时使用）

默认按 user_id 隔离；``share_within_tenant=True`` 时改为按 tenant_id 隔离（组织内共享）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from vanna.core.storage import Conversation, ConversationStore, Message
from vanna.core.user import User


class RedisConversationStore(ConversationStore):
    """Persistent, multi-tenant conversation store backed by Redis."""

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

    def _conv_key(self, conversation_id: str) -> str:
        return self._key("conv", conversation_id)

    def _msgs_key(self, conversation_id: str) -> str:
        return self._key("conv", conversation_id, "messages")

    def _tenant_of(self, user: User) -> str:
        try:
            return str(self._tenant_resolver(user))
        except Exception:
            return user.id

    def _scope_key(self, user: User) -> str:
        if self._share_within_tenant:
            return self._key("tenant", self._tenant_of(user), "convs")
        return self._key("user", user.id, "convs")

    def _scope_field_and_value(self, user: User) -> Tuple[str, str]:
        if self._share_within_tenant:
            return "tenant_id", self._tenant_of(user)
        return "user_id", user.id

    @staticmethod
    def _score(updated_at: datetime) -> float:
        return updated_at.timestamp()

    async def _load_by_id(self, conversation_id: str) -> Optional[Conversation]:
        data = await self._client.hgetall(self._conv_key(conversation_id))
        if not data:
            return None
        raw_messages = await self._client.lrange(
            self._msgs_key(conversation_id), 0, -1
        )
        messages = [Message.model_validate(json.loads(m)) for m in raw_messages]
        return Conversation(
            id=data["id"],
            user=User.model_validate(json.loads(data["user_json"])),
            messages=messages,
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            metadata=json.loads(data["metadata_json"])
            if data.get("metadata_json")
            else {},
        )

    async def close(self) -> None:
        # redis-py 5.x 推荐 aclose()，4.x 用 close()；优先 aclose 以兼容两者
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()
        else:  # pragma: no cover - legacy redis-py
            await self._client.close()

    # ------------------------------------------------------------------- facade

    async def create_conversation(
        self, conversation_id: str, user: User, initial_message: str
    ) -> Conversation:
        conversation = Conversation(
            id=conversation_id,
            user=user,
            messages=[Message(role="user", content=initial_message)],
        )
        existing = await self._client.hgetall(self._conv_key(conversation_id))
        if existing:
            # 不允许静默覆盖已存在的会话（否则会清空历史 / 重指派归属）
            raise ValueError(f"Conversation '{conversation_id}' already exists")
        pipe = self._client.pipeline()
        pipe.hset(
            self._conv_key(conversation_id),
            mapping={
                "id": conversation.id,
                "user_id": user.id,
                "tenant_id": self._tenant_of(user),
                "user_json": user.model_dump_json(),
                "created_at": conversation.created_at.isoformat(),
                "updated_at": conversation.updated_at.isoformat(),
                "metadata_json": json.dumps(
                    conversation.metadata, ensure_ascii=False
                ),
            },
        )
        pipe.delete(self._msgs_key(conversation_id))
        pipe.rpush(
            self._msgs_key(conversation_id),
            conversation.messages[0].model_dump_json(),
        )
        pipe.zadd(
            self._scope_key(user),
            {conversation_id: self._score(conversation.updated_at)},
        )
        await pipe.execute()
        return conversation

    async def get_conversation(
        self, conversation_id: str, user: User
    ) -> Optional[Conversation]:
        field, value = self._scope_field_and_value(user)
        data = await self._client.hgetall(self._conv_key(conversation_id))
        if not data or data.get(field) != value:
            return None
        return await self._load_by_id(conversation_id)

    async def update_conversation(self, conversation: Conversation) -> None:
        user = conversation.user
        conversation_id = conversation.id

        # 归属校验：已存在的会话必须属于当前用户/租户，否则拒绝写入
        field, value = self._scope_field_and_value(user)
        data = await self._client.hgetall(self._conv_key(conversation_id))
        if data and data.get(field) != value:
            raise ValueError(
                f"Conversation '{conversation_id}' does not belong to the "
                "current user/tenant"
            )

        existing = await self._client.llen(self._msgs_key(conversation_id))

        pipe = self._client.pipeline()
        pipe.hset(
            self._conv_key(conversation_id),
            mapping={
                "updated_at": conversation.updated_at.isoformat(),
                "metadata_json": json.dumps(
                    conversation.metadata, ensure_ascii=False
                ),
            },
        )
        if existing == 0:
            # 会话行尚不存在（首次落库）：补齐完整元数据
            pipe.hset(
                self._conv_key(conversation_id),
                mapping={
                    "id": conversation.id,
                    "user_id": user.id,
                    "tenant_id": self._tenant_of(user),
                    "user_json": user.model_dump_json(),
                    "created_at": conversation.created_at.isoformat(),
                },
            )
        for seq in range(existing, len(conversation.messages)):
            pipe.rpush(
                self._msgs_key(conversation_id),
                conversation.messages[seq].model_dump_json(),
            )
        pipe.zadd(
            self._scope_key(user),
            {conversation_id: self._score(conversation.updated_at)},
        )
        await pipe.execute()

    async def delete_conversation(self, conversation_id: str, user: User) -> bool:
        field, value = self._scope_field_and_value(user)
        data = await self._client.hgetall(self._conv_key(conversation_id))
        if not data or data.get(field) != value:
            return False
        pipe = self._client.pipeline()
        pipe.delete(self._conv_key(conversation_id))
        pipe.delete(self._msgs_key(conversation_id))
        pipe.zrem(self._scope_key(user), conversation_id)
        await pipe.execute()
        return True

    async def list_conversations(
        self, user: User, limit: int = 50, offset: int = 0
    ) -> List[Conversation]:
        ids = await self._client.zrange(
            self._scope_key(user), offset, offset + limit - 1, desc=True
        )
        conversations: List[Conversation] = []
        for conversation_id in ids:
            conversation = await self._load_by_id(conversation_id)
            if conversation is not None:
                conversations.append(conversation)
        return conversations
