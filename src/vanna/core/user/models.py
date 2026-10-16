"""
User domain models.

This module contains data models for user management.

本模块定义框架唯一的身份对象 User。它是 Vanna「用户感知（user-aware）」能力的地基：
工具可见性（access_groups）与 UI 信息可见性（UiFeatures）两道权限闸门，都只依赖
User.group_memberships 一个字段做分组交集判定。因此身份解析的准确性直接决定安全边界。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class User(BaseModel):
    """User model for authentication and scoping.

    已认证用户的运行时表示，由 UserResolver.resolve_user 从请求上下文产出。除 id 外
    其余字段皆可选，group_memberships 是权限判定的唯一依据（建议至少赋值 ['user']）。
    """

    # 用户唯一标识，贯穿会话、审计、记忆归属
    id: str = Field(description="Unique user identifier")
    # 用户名（可选）
    username: Optional[str] = Field(default=None, description="Username")
    # 邮箱（可选）
    email: Optional[str] = Field(default=None, description="User email")
    # 附加元数据，业务方可用它扩展用户画像
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional user metadata"
    )
    # 用户所属分组，是权限模型的核心：与 tool.access_groups / UiFeatures 做交集判定
    group_memberships: List[str] = Field(
        default_factory=list, description="Groups the user belongs to"
    )

    # 允许扩展额外字段，方便业务方携带自定义属性而不破坏模型
    model_config = ConfigDict(extra="allow")
