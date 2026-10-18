"""
SQLite-backed conversation store.

本模块提供内置的持久化会话存储：用 Python 标准库 sqlite3 落盘，零外部依赖，重启不丢
会话。所有查询都带身份作用域（user_id / tenant_id），实现多用户（多租户）隔离，默认
按 user_id 隔离；把 ``share_within_tenant=True`` 可切换为「同一租户内共享」。

设计要点（对齐主流方案）：
- 单文件数据库 + WAL，适合本地开发到中小规模生产；大规模生产可替换为 Postgres 等。
- 同步 sqlite3 调用统一放进 ``asyncio.to_thread``，避免阻塞事件循环。
- 写操作用 ``threading.RLock`` 串行化，保证连接在多线程下的安全。
- 消息整行存 JSON，保留完整字段（tool_calls / tool_call_id / metadata），读取时原样还原。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from vanna.core.storage import Conversation, ConversationStore, Message
from vanna.core.user import User

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_tenant
    ON conversations(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    conversation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    message_json TEXT NOT NULL,
    PRIMARY KEY (conversation_id, seq),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, seq);
"""


class SQLiteConversationStore(ConversationStore):
    """Persistent, multi-tenant conversation store backed by SQLite.

    Args:
        database_path: Path to the SQLite database file (created if missing).
        tenant_resolver: Optional callable(User) -> str deriving a tenant id.
            Defaults to the user id.
        share_within_tenant: If True, isolation is by tenant id (users in the same
            tenant share conversations). Defaults to False (per-user isolation).

    Example:
        store = SQLiteConversationStore("vanna.db")
        agent = Agent(..., conversation_store=store)
    """

    def __init__(
        self,
        database_path: str = "vanna_conversations.db",
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
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ helpers

    def _tenant_of(self, user: User) -> str:
        try:
            return str(self._tenant_resolver(user))
        except Exception:
            return user.id

    def _scope(self, user: User) -> Tuple[str, Any]:
        """Return the isolation column/value for the given user."""
        if self._share_within_tenant:
            return "tenant_id", self._tenant_of(user)
        return "user_id", user.id

    def _load_messages(self, conversation_id: str) -> List[Message]:
        rows = self._conn.execute(
            "SELECT message_json FROM messages WHERE conversation_id = ? ORDER BY seq",
            (conversation_id,),
        ).fetchall()
        return [Message.model_validate(json.loads(r["message_json"])) for r in rows]

    def _load_conversation(self, row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            user=User.model_validate(json.loads(row["user_json"])),
            messages=self._load_messages(row["id"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --------------------------------------------------------------- sync impls

    def _create_conversation_sync(
        self, conversation_id: str, user: User, initial_message: str
    ) -> Conversation:
        conversation = Conversation(
            id=conversation_id,
            user=user,
            messages=[Message(role="user", content=initial_message)],
        )
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation.id,)
            ).fetchone()
            if existing is not None:
                # 不允许静默覆盖已存在的会话（否则会清空历史 / 重指派归属）
                raise ValueError(
                    f"Conversation '{conversation.id}' already exists"
                )
            self._conn.execute(
                """
                INSERT INTO conversations
                    (id, user_id, tenant_id, user_json, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
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
            self._conn.execute(
                "INSERT INTO messages (conversation_id, seq, message_json) "
                "VALUES (?, ?, ?)",
                (conversation.id, 0, conversation.messages[0].model_dump_json()),
            )
            self._conn.commit()
        return conversation

    def _get_conversation_sync(
        self, conversation_id: str, user: User
    ) -> Optional[Conversation]:
        scope_col, scope_val = self._scope(user)
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM conversations WHERE id = ? AND {scope_col} = ?",
                (conversation_id, scope_val),
            ).fetchone()
            if not row:
                return None
            return self._load_conversation(row)

    def _update_conversation_sync(self, conversation: Conversation) -> None:
        user = conversation.user
        scope_col, scope_val = self._scope(user)
        with self._lock:
            # 1) 不存在则按当前归属创建；已存在（即使属于他人）则忽略
            self._conn.execute(
                """
                INSERT OR IGNORE INTO conversations
                    (id, user_id, tenant_id, user_json, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
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
            # 2) 仅允许更新当前作用域内的行；0 行说明会话属于他人
            cursor = self._conn.execute(
                f"UPDATE conversations SET updated_at = ?, metadata_json = ? "
                f"WHERE id = ? AND {scope_col} = ?",
                (
                    conversation.updated_at.isoformat(),
                    json.dumps(conversation.metadata, ensure_ascii=False),
                    conversation.id,
                    scope_val,
                ),
            )
            if cursor.rowcount == 0:
                self._conn.rollback()
                raise ValueError(
                    f"Conversation '{conversation.id}' does not belong to the "
                    "current user/tenant"
                )

            existing = self._conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ?",
                (conversation.id,),
            ).fetchone()["c"]

            # 只追加新消息（幂等：OR REPLACE 处理重复写入）
            for seq in range(existing, len(conversation.messages)):
                self._conn.execute(
                    "INSERT OR REPLACE INTO messages (conversation_id, seq, message_json) "
                    "VALUES (?, ?, ?)",
                    (
                        conversation.id,
                        seq,
                        conversation.messages[seq].model_dump_json(),
                    ),
                )
            self._conn.commit()

    def _delete_conversation_sync(self, conversation_id: str, user: User) -> bool:
        scope_col, scope_val = self._scope(user)
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM conversations WHERE id = ? AND {scope_col} = ?",
                (conversation_id, scope_val),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def _list_conversations_sync(
        self, user: User, limit: int, offset: int
    ) -> List[Conversation]:
        scope_col, scope_val = self._scope(user)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM conversations WHERE {scope_col} = ? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (scope_val, limit, offset),
            ).fetchall()
            return [self._load_conversation(row) for row in rows]

    # -------------------------------------------------------------- async facade

    async def create_conversation(
        self, conversation_id: str, user: User, initial_message: str
    ) -> Conversation:
        """Create a new conversation with the specified ID."""
        return await asyncio.to_thread(
            self._create_conversation_sync, conversation_id, user, initial_message
        )

    async def get_conversation(
        self, conversation_id: str, user: User
    ) -> Optional[Conversation]:
        """Get conversation by ID, scoped to the user/tenant."""
        return await asyncio.to_thread(
            self._get_conversation_sync, conversation_id, user
        )

    async def update_conversation(self, conversation: Conversation) -> None:
        """Persist the conversation (appending any new messages)."""
        await asyncio.to_thread(self._update_conversation_sync, conversation)

    async def delete_conversation(self, conversation_id: str, user: User) -> bool:
        """Delete a conversation owned by the user/tenant."""
        return await asyncio.to_thread(
            self._delete_conversation_sync, conversation_id, user
        )

    async def list_conversations(
        self, user: User, limit: int = 50, offset: int = 0
    ) -> List[Conversation]:
        """List conversations for the user/tenant (most recently updated first)."""
        return await asyncio.to_thread(
            self._list_conversations_sync, user, limit, offset
        )
