"""
Agent configuration.

This module contains configuration models that control agent behavior.

本模块集中定义了控制 Agent 行为的配置模型：UI 功能权限矩阵（UiFeatures）、
审计配置（AuditConfig）与 Agent 全局配置（AgentConfig）。这些配置是 Agent
在 Text2SQL 链路中"用户感知安全"与"行为可控"的集中体现——同一份配置同时
决定权限展示粒度与合规审计粒度。
"""

from typing import TYPE_CHECKING, Dict, List, Optional

from pydantic import BaseModel, Field

from .._compat import StrEnum

if TYPE_CHECKING:
    from ..user import User


class UiFeature(StrEnum):
    """UI 功能开关枚举。

    这是权限模型的"第二道闸门"（第一道是 ToolRegistry 的工具可见性）：它不
    控制工具能否被调用，而控制工具调用过程中的哪些信息可以展示给当前用户。
    在 Text2SQL 场景中，最关键的是 tool_arguments——SQL 语句本身即工具参数，
    默认仅 admin 可见，普通用户只看到结果表格与图表。
    """

    # 是否向用户展示工具名称（如 run_sql / visualize_data）
    UI_FEATURE_SHOW_TOOL_NAMES = "tool_names"
    # 是否向用户展示工具调用参数（Text2SQL 中 SQL 语句即参数，默认仅 admin 可见）
    UI_FEATURE_SHOW_TOOL_ARGUMENTS = "tool_arguments"
    # 是否向用户展示工具执行失败的详细错误信息
    UI_FEATURE_SHOW_TOOL_ERROR = "tool_error"
    # 是否把"LLM 在调用工具前输出的说明文字"作为聊天消息展示
    UI_FEATURE_SHOW_TOOL_INVOCATION_MESSAGE_IN_CHAT = "tool_invocation_message_in_chat"
    # 是否向用户展示记忆检索（AgentMemory）的详细结果
    UI_FEATURE_SHOW_MEMORY_DETAILED_RESULTS = "memory_detailed_results"


# Optional: you can also define defaults if you want a shared baseline
# 默认的 UI 功能权限基线：工具名称对 admin/user 可见；参数、错误、记忆详情等
# 敏感信息仅 admin 可见。体现"给 LLM 的信息"与"给用户展示的信息"分离原则。
DEFAULT_UI_FEATURES: Dict[str, List[str]] = {
    UiFeature.UI_FEATURE_SHOW_TOOL_NAMES: ["admin", "user"],
    UiFeature.UI_FEATURE_SHOW_TOOL_ARGUMENTS: ["admin"],
    UiFeature.UI_FEATURE_SHOW_TOOL_ERROR: ["admin"],
    UiFeature.UI_FEATURE_SHOW_TOOL_INVOCATION_MESSAGE_IN_CHAT: ["admin"],
    UiFeature.UI_FEATURE_SHOW_MEMORY_DETAILED_RESULTS: ["admin"],
}


class UiFeatures(BaseModel):
    """UI features with group-based access control using the same pattern as tools.

    Each field specifies which groups can access that UI feature.
    Empty list means the feature is accessible to all users.
    Uses the same intersection logic as tool access control.

    维护"UI 功能名 → 允许访问的分组列表"的映射，并复用与工具权限完全一致的
    分组交集判定逻辑（set(user.group_memberships) & set(allowed_groups)）。
    空列表表示该功能对所有用户开放。
    """

    # Custom features for extensibility
    # 功能名到允许访问分组的映射；默认从 DEFAULT_UI_FEATURES 拷贝，避免共享可变默认值
    feature_group_access: Dict[str, List[str]] = Field(
        default_factory=lambda: DEFAULT_UI_FEATURES.copy(),
        description="Which groups can access UI features",
    )

    def can_user_access_feature(self, feature_name: str, user: "User") -> bool:
        """Check if user can access a UI feature using same logic as tools.

        Args:
            feature_name: Name of the UI feature to check
            user: User object with group_memberships

        Returns:
            True if user has access, False otherwise

        判定逻辑与工具权限一致：未注册的功能一律拒绝；空分组表示人人可访问；
        否则要求用户分组与功能分组存在交集。
        """
        # Then try custom features
        if feature_name in self.feature_group_access:
            allowed_groups = self.feature_group_access[feature_name]
        else:
            # Feature doesn't exist, deny access
            # 未注册的功能默认拒绝（fail-closed）
            return False

        # Empty list means all users can access (same as tools)
        if not allowed_groups:
            return True

        # Same intersection logic as tool access control
        # 与 ToolRegistry._validate_tool_permissions 相同的分组交集判定
        user_groups = set(user.group_memberships)
        feature_groups = set(allowed_groups)
        return bool(user_groups & feature_groups)

    def register_feature(self, name: str, access_groups: List[str]) -> None:
        """Register a custom UI feature with group access control.

        Args:
            name: Name of the custom feature
            access_groups: List of groups that can access this feature

        供业务方扩展自定义 UI 功能开关：注册功能名与其可见分组，随后可在
        Agent 或自定义组件中通过 can_user_access_feature 做细粒度权限判断。
        """
        self.feature_group_access[name] = access_groups


class AuditConfig(BaseModel):
    """Configuration for audit logging.

    审计配置：控制哪些事件需要落审计日志，以及敏感信息（如 token、password）
    是否需要脱敏。审计是 Text2SQL 企业级合规的关键——每一次 SQL 执行、权限
    判定、AI 回复都可被追溯。该配置由 Agent 在初始化时回注入 ToolRegistry。
    """

    # 是否启用审计总开关（关闭后 Agent 不会向 ToolRegistry 注入审计 logger）
    enabled: bool = Field(default=True, description="Enable audit logging")
    # 是否记录工具权限判定结果（谁被允许/拒绝访问某工具）
    log_tool_access_checks: bool = Field(
        default=True, description="Log tool access permission checks"
    )
    # 是否记录工具调用（含参数，可配合脱敏）
    log_tool_invocations: bool = Field(
        default=True, description="Log tool invocations with parameters"
    )
    # 是否记录工具执行结果（含耗时、结果大小，但不含完整结果内容）
    log_tool_results: bool = Field(
        default=True, description="Log tool execution results"
    )
    # 是否记录 UI 功能可见性判定（较频繁，默认关闭以避免噪音）
    log_ui_feature_checks: bool = Field(
        default=False, description="Log UI feature access checks (can be noisy)"
    )
    # 是否记录 AI 生成的回复
    log_ai_responses: bool = Field(
        default=True, description="Log AI-generated responses"
    )
    # 是否在日志中保留 AI 回复全文（隐私考量，默认只记长度与哈希）
    include_full_ai_responses: bool = Field(
        default=False,
        description="Include full AI response text in logs (privacy concern)",
    )
    # 是否对工具参数中的敏感字段（password/token/api_key 等）脱敏
    sanitize_tool_parameters: bool = Field(
        default=True, description="Sanitize sensitive parameters (passwords, tokens)"
    )


class AgentConfig(BaseModel):
    """Configuration for agent behavior.

    Agent 全局行为配置：控制工具循环上限、流式开关、会话持久化、采样参数等。
    其中 max_tool_iterations 是 Text2SQL 链路的"熔断器"——限制 LLM 在一个
    用户回合内最多调用多少次工具（含 SQL 生成/修复的重试次数），防止失控。
    """

    # 单个用户回合内工具调用的最大次数（LLM 生成/修复 SQL 的重试上限）
    max_tool_iterations: int = Field(default=10, gt=0)
    # 是否启用 LLM 流式响应（注意：当前实现是组件级流式，非 token 级打字机）
    stream_responses: bool = Field(default=True)
    # 是否在处理结束后自动持久化会话
    auto_save_conversations: bool = Field(default=True)
    # 是否包含"思考中"指示（agent.py 中该逻辑目前仅为 TODO 占位，未实现）
    include_thinking_indicators: bool = Field(default=True)
    # LLM 采样温度（0.0 ~ 2.0）
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # 单次 LLM 请求的最大生成 token 数（None 表示不限制）
    max_tokens: Optional[int] = Field(default=None, gt=0)
    # UI 功能权限矩阵（见 UiFeatures）
    ui_features: UiFeatures = Field(default_factory=UiFeatures)
    # 审计配置（见 AuditConfig）
    audit_config: AuditConfig = Field(default_factory=AuditConfig)
