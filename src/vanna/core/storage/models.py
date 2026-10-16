"""
Storage domain models.

This module contains data models for conversation storage.

本模块定义会话持久化的数据模型：Message（单条消息）与 Conversation（会话容器）。
注意它们与 core/llm/models.py 里的 LlmMessage 是两套模型——本模块面向「存储」，
LlmMessage 面向「LLM 通信」，由 Agent._build_llm_request 在每次请求前完成映射，
从而让存储格式与模型协议解耦。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..tool.models import ToolCall
from ..user.models import User


class Message(BaseModel):
    """Single message in a conversation.

    会话中的单条消息。role 遵循 system/user/assistant/tool 约定；assistant 消息可携带
    tool_calls，tool 消息用 tool_call_id 回指对应的工具调用（OpenAI 协议要求）。
    """

    # 消息角色（user/assistant/system/tool）
    role: str = Field(description="Message role (user/assistant/system/tool)")
    # 消息正文
    content: str = Field(description="Message content")
    # 消息时间戳，默认取当前 UTC 时间
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    # 附加元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # assistant 角色发起的工具调用
    tool_calls: Optional[List[ToolCall]] = Field(default=None)
    # 若为 tool 角色消息，此 ID 用于关联对应的 assistant tool_call
    tool_call_id: Optional[str] = Field(
        default=None, description="ID if this is a tool response"
    )


class Conversation(BaseModel):
    """Conversation containing multiple messages.

    一次多轮对话的聚合容器，归属明确（user 字段），是会话级数据隔离的依据。
    """

    # 会话唯一标识（客户端或服务端生成，用于跨请求关联）
    id: str = Field(description="Unique conversation identifier")
    # 会话归属用户，存储层据此做数据隔离（防止跨用户串号）
    user: User = Field(description="User this conversation belongs to")
    # 会话内的消息序列
    messages: List[Message] = Field(
        default_factory=list, description="Messages in conversation"
    )
    # 创建时间
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # 最近更新时间（每次 add_message 都会刷新）
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # 附加元数据
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional conversation metadata"
    )

    def add_message(self, message: Message) -> None:
        """Add a message to the conversation.

        追加一条消息并刷新 updated_at，供排序、过期清理等场景使用。
        """
        self.messages.append(message)
        self.updated_at = datetime.utcnow()
