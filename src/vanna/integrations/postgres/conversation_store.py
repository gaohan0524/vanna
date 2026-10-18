"""
PostgreSQL-backed conversation store.

本模块提供 PostgreSQL 持久化会话存储：复用已有的 psycopg2 依赖（``vanna[postgres]``），
把同步驱动调用放进 ``asyncio.to_thread`` 以避免阻塞事件循环。所有查询都带身份作用域
（user_id / tenant_id），默认按 user_id 隔离，可用 ``share_within_tenant`` 切换为租户内共享。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from vanna.core.storage import Conversation, ConversationStore, Message
from vanna.core.user import User

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vanna_conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_vanna_conversations_user
    ON vanna_conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vanna_conversations_tenant
    ON vanna_conversations(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS vanna_messages (
    conversation_id TEXT NOT NULL
        REFERENCES vanna_conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    message_json TEXT NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_vanna_messages_conversation
    ON vanna_messages(conversation_id, seq);
"""


class PostgresConversationStore(ConversationStore):
    """Persistent, multi-tenant conversation store backed by PostgreSQL.

    Args:
        connection_string: PostgreSQL DSN, e.g. ``postgresql://user:pass@host:5432/db``.
        host/port/database/user/password: Alternative to ``connection_string``.
        tenant_resolver: Optional callable(User) -> str deriving a tenant id.
            Defaults to the user id.
        share_within_tenant: If True, isolation is by tenant id (org-shared), else by user.
        **kwargs: Extra psycopg2 connection parameters (sslmode, connect_timeout, ...).
    """

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
        except Exception as e:  # pragma: no cover - import-time error surface
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

    def _tenant_of(self, user: User) -> str:
        try:
            return str(self._tenant_resolver(user))
        except Exception:
            return user.id

    def _scope(self, user: User) -> Tuple[str, Any]:
        if self._share_within_tenant:
            return "tenant_id", self._tenant_of(user)
        return "user_id", user.id

    def _load_conversation(self, conn, row: dict) -> Conversation:
        with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT message_json FROM vanna_messages "
                "WHERE conversation_id = %s ORDER BY seq",
                (row["id"],),
            )
            messages = [
                Message.model_validate(json.loads(r["message_json"])) for r in cur
            ]
        return Conversation(
            id=row["id"],
            user=User.model_validate(json.loads(row["user_json"])),
            messages=messages,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    # --------------------------------------------------------------- sync impls

    def _create_sync(
        self, conversation_id: str, user: User, initial_message: str
    ) -> Conversation:
        conversation = Conversation(
            id=conversation_id,
            user=user,
            messages=[Message(role="user", content=initial_message)],
        )
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vanna_conversations
                        (id, user_id, tenant_id, user_json,
                         created_at, updated_at, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        updated_at = EXCLUDED.updated_at,
                        metadata_json = EXCLUDED.metadata_json
                    """,
                    (
                        conversation.id,
                        user.id,
                        self._tenant_of(user),
                        user.model_dump_json(),
                        conversation.created_at.isoformat(),
                        conversation.updated_at.isoformat(),
                        json.dumps(conversation.metadata, ensure_ascii=False),
                    ),
                )
                cur.execute(
                    "DELETE FROM vanna_messages WHERE conversation_id = %s",
                    (conversation.id,),
                )
                cur.execute(
                    "INSERT INTO vanna_messages (conversation_id, seq, message_json) "
                    "VALUES (%s, %s, %s)",
                    (conversation.id, 0, conversation.messages[0].model_dump_json()),
                )
            conn.commit()
        finally:
            conn.close()
        return conversation

    def _get_sync(self, conversation_id: str, user: User) -> Optional[Conversation]:
        scope_col, scope_val = self._scope(user)
        conn = self._connect()
        try:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM vanna_conversations "
                    f"WHERE id = %s AND {scope_col} = %s",
                    (conversation_id, scope_val),
                )
                row = cur.fetchone()
            if not row:
                return None
            return self._load_conversation(conn, row)
        finally:
            conn.close()

    def _update_sync(self, conversation: Conversation) -> None:
        user = conversation.user
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vanna_conversations
                        (id, user_id, tenant_id, user_json,
                         created_at, updated_at, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        updated_at = EXCLUDED.updated_at,
                        metadata_json = EXCLUDED.metadata_json
                    """,
                    (
                        conversation.id,
                        user.id,
                        self._tenant_of(user),
                        user.model_dump_json(),
                        conversation.created_at.isoformat(),
                        conversation.updated_at.isoformat(),
                        json.dumps(conversation.metadata, ensure_ascii=False),
                    ),
                )
                cur.execute(
                    "SELECT COUNT(*) AS c FROM vanna_messages WHERE conversation_id = %s",
                    (conversation.id,),
                )
                existing = cur.fetchone()[0]
                for seq in range(existing, len(conversation.messages)):
                    cur.execute(
                        "INSERT INTO vanna_messages (conversation_id, seq, message_json) "
                        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (
                            conversation.id,
                            seq,
                            conversation.messages[seq].model_dump_json(),
                        ),
                    )
            conn.commit()
        finally:
            conn.close()

    def _delete_sync(self, conversation_id: str, user: User) -> bool:
        scope_col, scope_val = self._scope(user)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM vanna_conversations "
                    f"WHERE id = %s AND {scope_col} = %s",
                    (conversation_id, scope_val),
                )
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            conn.close()

    def _list_sync(self, user: User, limit: int, offset: int) -> List[Conversation]:
        scope_col, scope_val = self._scope(user)
        conn = self._connect()
        try:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM vanna_conversations WHERE {scope_col} = %s "
                    "ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                    (scope_val, limit, offset),
                )
                rows = cur.fetchall()
            return [self._load_conversation(conn, row) for row in rows]
        finally:
            conn.close()

    # -------------------------------------------------------------- async facade

    async def create_conversation(
        self, conversation_id: str, user: User, initial_message: str
    ) -> Conversation:
        return await asyncio.to_thread(
            self._create_sync, conversation_id, user, initial_message
        )

    async def get_conversation(
        self, conversation_id: str, user: User
    ) -> Optional[Conversation]:
        return await asyncio.to_thread(self._get_sync, conversation_id, user)

    async def update_conversation(self, conversation: Conversation) -> None:
        await asyncio.to_thread(self._update_sync, conversation)

    async def delete_conversation(self, conversation_id: str, user: User) -> bool:
        return await asyncio.to_thread(self._delete_sync, conversation_id, user)

    async def list_conversations(
        self, user: User, limit: int = 50, offset: int = 0
    ) -> List[Conversation]:
        return await asyncio.to_thread(self._list_sync, user, limit, offset)
