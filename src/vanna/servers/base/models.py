"""
Request and response models for server endpoints.

本模块定义服务端入参与出参的数据契约：ChatRequest（请求）、ChatStreamChunk（流式
分片）、ChatResponse（轮询聚合）。其中 ChatStreamChunk.from_component 是「UI 组件 →
线上传输格式」的转换点——它调用 serialize_for_frontend 把 rich/simple 组件序列化为
字典，构成前后端之间的 wire 协议。
"""

import time
import uuid
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from ...components import UiComponent, RichComponent
from ...core.user.request_context import RequestContext


class ChatRequest(BaseModel):
    """Request model for chat endpoints.

    客户端发来的聊天请求。request_context 由 Web 层填充（见 routes.py），UserResolver
    据此解析身份；message 与 conversation_id 驱动一轮对话。
    """

    # 用户输入的自然语言问题（Text2SQL 的起点）
    message: str = Field(description="User message")
    # 会话 ID；为空则由服务端新建（多轮对话的关键）
    conversation_id: Optional[str] = Field(default=None, description="Conversation ID")
    # 客户端提供的追踪 ID，用于端到端链路关联
    request_id: Optional[str] = Field(
        default=None, description="Request ID for tracing"
    )
    # 请求上下文（cookie/header/IP 等），由 Web 层填充后供身份解析
    request_context: RequestContext = Field(
        default_factory=RequestContext,
        description="Request context for user resolution",
    )
    # 附加元数据（可承载前端自定义标记，如 starter_ui_request）
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class ChatStreamChunk(BaseModel):
    """Single chunk in a streaming chat response.

    流式响应的单个分片，是前后端 wire 协议的核心载体：rich 字段承载富客户端组件，
    simple 字段承载降级客户端的简化表示，二者由同一 UiComponent 序列化而来。
    """

    # 富客户端组件数据（serialize_for_frontend 的输出，字段收在 data 下）
    rich: Dict[str, Any] = Field(description="Rich component data for advanced UIs")
    # 简化组件数据（仅文本/图片/链接，供无富渲染能力的客户端使用）
    simple: Optional[Dict[str, Any]] = Field(
        default=None, description="Simple component data for basic UIs"
    )

    # Stream metadata
    # 所属会话 ID（前端据此维护多轮上下文）
    conversation_id: str = Field(description="Conversation ID")
    # 请求追踪 ID
    request_id: str = Field(description="Request ID")
    # 分片生成时间戳
    timestamp: float = Field(default_factory=time.time, description="Timestamp")

    @classmethod
    def from_component(
        cls,
        component: Union[UiComponent, RichComponent],
        conversation_id: str,
        request_id: str,
    ) -> "ChatStreamChunk":
        """Create chunk from UI component or rich component.

        把 Agent 产出的 UiComponent（或纯 RichComponent）转换为传输分片：rich 取
        rich_component.serialize_for_frontend()，simple 取 simple_component 的同名
        序列化结果（若存在）。这是组件到线上 JSON 的唯一转换入口。
        """

        if isinstance(component, UiComponent):
            # Full UiComponent with both rich and simple
            # 完整组件：同时序列化富表示与简化表示，保证两类客户端都能渲染
            rich_data = component.rich_component.serialize_for_frontend()
            simple_data = None
            if component.simple_component:
                simple_data = component.simple_component.serialize_for_frontend()

            return cls(
                rich=rich_data,
                simple=simple_data,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        # Rich component only (no simple fallback)
        # 仅富组件（无降级表示）：simple 置空
        rich_data = component.serialize_for_frontend()
        return cls(
            rich=rich_data,
            simple=None,
            conversation_id=conversation_id,
            request_id=request_id,
        )

class ChatResponse(BaseModel):
    """Complete chat response for polling endpoints.

    轮询端点的完整响应：把整条流的所有分片聚合成一个对象返回，供一次性消费。
    """

    # 全部流式分片（按产出顺序）
    chunks: List[ChatStreamChunk] = Field(description="Response chunks")
    # 会话 ID
    conversation_id: str = Field(description="Conversation ID")
    # 请求追踪 ID
    request_id: str = Field(description="Request ID")
    # 分片总数
    total_chunks: int = Field(description="Total number of chunks")

    @classmethod
    def from_chunks(cls, chunks: List[ChatStreamChunk]) -> "ChatResponse":
        """Create response from chunks.

        从分片列表聚合出完整响应；会话/请求 ID 取自首个分片，空列表时返回空响应。
        """
        if not chunks:
            return cls(chunks=[], conversation_id="", request_id="", total_chunks=0)

        return cls(
            chunks=chunks,
            conversation_id=chunks[0].conversation_id,
            request_id=chunks[0].request_id,
            total_chunks=len(chunks),
        )
