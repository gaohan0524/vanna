"""
Storage domain interface.

This module contains the abstract base class for conversation storage.

本模块定义会话持久化的抽象基类 ConversationStore，是「换存储后端」的唯一契约：内存、
数据库、Redis 等只需实现这 5 个方法即可接入。默认实现是 integrations/local 中的
MemoryConversationStore（无外部依赖，仅用于开发/测试）。
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from .models import Conversation
from ..user.models import User


class ConversationStore(ABC):
    """Abstract base class for conversation storage.

    会话存储抽象基类。所有方法都以 user 为作用域入参，强制实现「会话归属用户」的
    数据隔离，防止跨用户越权读取。
    """

    @abstractmethod
    async def create_conversation(
        self, conversation_id: str, user: User, initial_message: str
    ) -> Conversation:
        """Create a new conversation with the specified ID.

        按指定 ID 新建会话（通常把首条用户消息作为 initial_message 一并写入）。
        """
        pass

    @abstractmethod
    async def get_conversation(
        self, conversation_id: str, user: User
    ) -> Optional[Conversation]:
        """Get conversation by ID, scoped to user.

        按 ID 读取会话，且必须校验归属用户；非本人会话应返回 None 而非报错泄露存在性。
        """
        pass

    @abstractmethod
    async def update_conversation(self, conversation: Conversation) -> None:
        """Update conversation with new messages.

        持久化会话的最新状态（通常是追加了消息或修改了元数据后的完整对象）。
        """
        pass

    @abstractmethod
    async def delete_conversation(self, conversation_id: str, user: User) -> bool:
        """Delete conversation.

        删除指定用户拥有的会话，返回是否成功删除。
        """
        pass

    @abstractmethod
    async def list_conversations(
        self, user: User, limit: int = 50, offset: int = 0
    ) -> List[Conversation]:
        """List conversations for user.

        分页列出该用户的会话，用于历史会话选择界面。
        """
        pass
