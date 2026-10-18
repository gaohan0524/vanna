"""
MySQL-backed conversation store.

本模块提供 MySQL 持久化会话存储：复用已有的 PyMySQL 依赖（``vanna[mysql]``），把同步
驱动调用放进 ``asyncio.to_thread``。所有查询按 user_id / tenant_id 作用域隔离，默认按
user_id 隔离，可用 ``share_within_tenant`` 切换为租户内共享。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from vanna.core.storage import Conversation, ConversationStore, Message
from vanna.core.user import User

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS vanna_conversations (
        id VARCHAR(255) PRIMARY KEY,
        user_id VARCHAR(255) NOT NULL,
        tenant_id VARCHAR(255) NOT NULL,
        user_json LONGTEXT NOT NULL,
        created_at VARCHAR(64) NOT NULL,
        updated_at VARCHAR(64) NOT NULL,
        metadata_json LONGTEXT,
        INDEX idx_vanna_conversations_user (user_id, updated_at),
        INDEX idx_vanna_conversations_tenant (tenant_id, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS vanna_messages (
        conversation_id VARCHAR(255) NOT NULL,
        seq INT NOT NULL,
        message_json LONGTEXT NOT NULL,
        PRIMARY KEY (conversation_id, seq),
        CONSTRAINT fk_vanna_messages_conversation
            FOREIGN KEY (conversation_id)
            REFERENCES vanna_conversations(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


class MySQLConversationStore(ConversationStore):
    """Persistent, multi-tenant conversation store backed by MySQL."""

    def __init__(
        self,
        host: Optional[str] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        port: int = 3306,
        *,
        tenant_resolver: Optional[Callable[[User], str]] = None,
        share_within_tenant: bool = False,
        **kwargs: Any,
    ) -> None:
        try:
            import pymysql
            import pymysql.cursors
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "PyMySQL package is required. Install with: pip install 'vanna[mysql]'"
            ) from e

        if not (host and database and user):
            raise ValueError("host, database and user are required for MySQL backend")

        self._pymysql = pymysql
        self._host = host
        self._database = database
        self._user = user
        self._password = password
        self._port = port
        self._kwargs = kwargs
        self._tenant_resolver: Callable[[User], str] = tenant_resolver or (
            lambda u: u.id
        )
        self._share_within_tenant = share_within_tenant
        self._init_schema()

    # ------------------------------------------------------------------ helpers

    def _connect(self):
        return self._pymysql.connect(
            host=self._host,
            user=self._user,
            password=self._password,
            database=self._database,
            port=self._port,
            charset="utf8mb4",
            cursorclass=self._pymysql.cursors.DictCursor,
            **self._kwargs,
        )

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                for statement in _SCHEMA_STATEMENTS:
                    cur.execute(statement)
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
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_json FROM vanna_messages "
                "WHERE conversation_id = %s ORDER BY seq",
                (row["id"],),
            )
            messages = [Message.model_validate(json.loads(r["message_json"])) for r in cur]
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
                    ON DUPLICATE KEY UPDATE
                        updated_at = VALUES(updated_at),
                        metadata_json = VALUES(metadata_json)
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
            with conn.cursor() as cur:
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
                    ON DUPLICATE KEY UPDATE
                        updated_at = VALUES(updated_at),
                        metadata_json = VALUES(metadata_json)
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
                existing = cur.fetchone()["c"]
                for seq in range(existing, len(conversation.messages)):
                    cur.execute(
                        "INSERT IGNORE INTO vanna_messages "
                        "(conversation_id, seq, message_json) VALUES (%s, %s, %s)",
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
            with conn.cursor() as cur:
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
