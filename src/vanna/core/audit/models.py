"""
Audit event models.

This module contains data models for audit logging events.

本模块定义审计日志的事件模型：事件类型枚举与各类事件。审计是 Text2SQL 企业级
合规的核心——把「谁在何时执行了什么 SQL、被授予/拒绝了什么权限、AI 回复了什么」
结构化记录，供安全审计、合规追溯与问题排查使用。
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .._compat import StrEnum


class AuditEventType(StrEnum):
    """Types of audit events.

    审计事件类型：访问控制判定、工具调用/结果、会话、安全四类。
    """

    # Access control events
    # 工具访问权限判定
    TOOL_ACCESS_CHECK = "tool_access_check"
    # UI 功能访问权限判定
    UI_FEATURE_ACCESS_CHECK = "ui_feature_access_check"

    # Tool execution events
    # 工具调用
    TOOL_INVOCATION = "tool_invocation"
    # 工具执行结果
    TOOL_RESULT = "tool_result"

    # Conversation events
    # 收到用户消息
    MESSAGE_RECEIVED = "message_received"
    # 生成 AI 回复
    AI_RESPONSE_GENERATED = "ai_response_generated"
    # 创建会话
    CONVERSATION_CREATED = "conversation_created"

    # Security events
    # 访问被拒绝
    ACCESS_DENIED = "access_denied"
    # 认证尝试
    AUTHENTICATION_ATTEMPT = "authentication_attempt"


class AuditEvent(BaseModel):
    """Base audit event with common fields.

    审计事件基类，承载所有事件共有的通用字段：事件标识、用户上下文、请求上下文
    与隐私标记。
    """

    # 事件唯一标识
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # 事件类型
    event_type: AuditEventType
    # 事件时间
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # User context
    # 用户 ID
    user_id: str
    # 用户名（可选）
    username: Optional[str] = None
    # 用户邮箱（可选）
    user_email: Optional[str] = None
    # 用户所属分组（权限判定依据）
    user_groups: List[str] = Field(default_factory=list)

    # Request context
    # 会话 ID
    conversation_id: str
    # 请求追踪 ID
    request_id: str
    # 客户端 IP（可选）
    remote_addr: Optional[str] = None

    # Event-specific data
    # 事件特有数据
    details: Dict[str, Any] = Field(default_factory=dict)

    # Privacy/redaction markers
    # 是否包含个人身份信息（PII）
    contains_pii: bool = False
    # 已脱敏的字段名列表
    redacted_fields: List[str] = Field(default_factory=list)


class ToolAccessCheckEvent(AuditEvent):
    """Audit event for tool access permission checks.

    工具访问权限判定事件：记录某用户访问某工具是否被授予，以及要求的分组。
    """

    event_type: AuditEventType = AuditEventType.TOOL_ACCESS_CHECK
    # 被访问的工具名
    tool_name: str
    # 是否授予访问
    access_granted: bool
    # 访问所需的分组
    required_groups: List[str] = Field(default_factory=list)
    # 拒绝原因（可选）
    reason: Optional[str] = None


class ToolInvocationEvent(AuditEvent):
    """Audit event for actual tool executions.

    工具调用事件：记录实际执行了哪个工具、传了什么参数（支持脱敏）。
    """

    event_type: AuditEventType = AuditEventType.TOOL_INVOCATION
    # 工具调用 ID
    tool_call_id: str
    # 工具名
    tool_name: str

    # Parameters with sanitization support
    # 调用参数（可能已脱敏）
    parameters: Dict[str, Any] = Field(default_factory=dict)
    # 参数是否已脱敏
    parameters_sanitized: bool = False

    # UI context at invocation time
    # 调用时用户可见的 UI 功能列表
    ui_features_available: List[str] = Field(default_factory=list)


class ToolResultEvent(AuditEvent):
    """Audit event for tool execution results.

    工具执行结果事件：记录成败、耗时、结果大小（不含完整内容）。
    """

    event_type: AuditEventType = AuditEventType.TOOL_RESULT
    # 工具调用 ID
    tool_call_id: str
    # 工具名
    tool_name: str
    # 是否成功
    success: bool
    # 失败原因（可选）
    error: Optional[str] = None
    # 执行耗时（毫秒）
    execution_time_ms: float = 0.0

    # Result metadata (without full content for size)
    # 结果字节数（用于体积监控，不存完整内容）
    result_size_bytes: Optional[int] = None
    # 结果的 UI 组件类型
    ui_component_type: Optional[str] = None


class UiFeatureAccessCheckEvent(AuditEvent):
    """Audit event for UI feature access checks.

    UI 功能访问判定事件：记录某用户查看某 UI 功能（如 SQL 参数）是否被授予。
    """

    event_type: AuditEventType = AuditEventType.UI_FEATURE_ACCESS_CHECK
    # 功能名（如 tool_arguments）
    feature_name: str
    # 是否授予访问
    access_granted: bool
    # 访问所需的分组
    required_groups: List[str] = Field(default_factory=list)


class AiResponseEvent(AuditEvent):
    """Audit event for AI-generated responses.

    AI 回复事件：记录回复的长度、哈希（完整性校验）、模型信息与工具调用数。
    默认不保存全文（隐私考量），仅保存哈希。
    """

    event_type: AuditEventType = AuditEventType.AI_RESPONSE_GENERATED

    # Response metadata
    # 回复字符数
    response_length_chars: int
    # 回复 token 数（可选）
    response_length_tokens: Optional[int] = None

    # Full text (optional, configurable)
    # 回复全文（可选，默认不存）
    response_text: Optional[str] = None
    # 回复哈希（SHA256，用于完整性校验/去重）
    response_hash: str  # SHA256 for integrity verification

    # Model info
    # 模型名（可选）
    model_name: Optional[str] = None
    # 采样温度（可选）
    temperature: Optional[float] = None

    # Tool calls in response
    # 回复中的工具调用次数
    tool_calls_count: int = 0
    # 回复中调用的工具名列表
    tool_names: List[str] = Field(default_factory=list)
