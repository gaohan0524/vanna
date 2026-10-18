"""
Tool domain models.

This module contains data models for tool execution.

本模块定义了工具执行链路的全部数据契约：LLM 发起的调用（ToolCall）、传给工具的
上下文（ToolContext）、执行结果（ToolResult）、给 LLM 的工具描述（ToolSchema）与
拒绝执行（ToolRejection）。它们是 Agent 编排层、ToolRegistry 注册层与具体工具实现
三方之间唯一的通信载体，全部使用 Pydantic 模型以保证参数校验与序列化的一致性。
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, Field

# Import AgentMemory at runtime for Pydantic model resolution
from vanna.capabilities.agent_memory import AgentMemory

if TYPE_CHECKING:
    from ..components import UiComponent
    from ..user.models import User
    from ..observability import ObservabilityProvider


class ToolCall(BaseModel):
    """Represents a tool call from the LLM.

    LLM 在工具循环中发起的一次工具调用。它来自 LlmResponse.tool_calls，字段一一对应：
    id 用于在循环里把「调用」与「结果」配对，name/arguments 则是 LLM 的原始意图输出。
    """

    # 该次工具调用的唯一标识，用于把工具结果与调用一一对应（尤其多工具并行时）
    id: str = Field(description="Unique identifier for this tool call")
    # 要执行的工具名（如 run_sql），由 LLM 产出，registry 会校验其是否注册
    name: str = Field(description="Name of the tool to execute")
    # LLM 给出的原始参数（Text2SQL 中即 SQL 语句），未经校验，交由 registry 用 Pydantic 模型校验
    arguments: Dict[str, Any] = Field(description="Raw arguments from LLM")


class ToolContext(BaseModel):
    """Context passed to all tool executions.

    唯一传给工具的参数包，同时承担三种角色：
    1. 身份凭证：user（工具据此做行级安全 RLS）；
    2. 能力句柄：agent_memory / observability_provider（工具自己能读写记忆、埋点）；
    3. 侧信道：metadata（由 ToolContextEnricher 注入，可放任意会话级附加数据）。
    """

    # 当前用户，是工具实现行级安全（如 SQL 加租户过滤）的依据
    user: "User"  # Forward reference to avoid circular import
    # 所属会话，工具可据此关联上下文
    conversation_id: str
    # 本次请求的唯一追踪 ID，用于关联日志与链路追踪
    request_id: str = Field(description="Unique request identifier for tracing")
    # 智能体记忆句柄，工具可读写（例如把成功的 SQL 保存为经验）
    agent_memory: AgentMemory = Field(
        description="Agent memory for tool usage learning"
    )
    # 附加元数据，由 context_enrichers 注入（如 UI 功能可见性列表），按需读取
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 可选的可观测性提供者，工具内部可自行创建 span / 记录指标
    observability_provider: Optional["ObservabilityProvider"] = Field(
        default=None,
        description="Optional observability provider for metrics and spans",
    )

    class Config:
        # 允许 metadata 等字段容纳非 Pydantic 类型（如回调、连接对象）
        arbitrary_types_allowed = True


class ToolResult(BaseModel):
    """Result from tool execution.

    Changes:
    - `result_for_llm`: string that will be sent back to the LLM.
    - `ui_component`: optional UI payload for rendering in clients.

    工具执行的统一返回结构，是「给 LLM 的信息」与「给用户展示的信息」分离的核心载体：
    result_for_llm 只回喂给模型（含成功摘要或失败原因），ui_component 只用于前端渲染，
    两者可以完全不同。这也是 SQL 执行失败时能被 LLM 自我修复的机制基础。
    """

    # 执行是否成功，决定 agent 走「回喂结果」还是「回喂错误」
    success: bool = Field(description="Whether execution succeeded")
    # 回喂 LLM 的文本：成功时为结果摘要，失败时为错误说明（供模型下一轮自我纠正）
    result_for_llm: str = Field(description="String content to send back to the LLM")
    # 可选的前端 UI 组件（表格/图表/状态卡），其可见性由 UiFeature 权限控制
    ui_component: Optional["UiComponent"] = Field(
        default=None, description="Optional UI component for rendering"
    )
    # 失败原因；与 result_for_llm 分离，便于审计与结构化处理
    error: Optional[str] = Field(default=None, description="Error message if failed")
    # 附加元数据，如查询耗时、行数等
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolSchema(BaseModel):
    """Schema describing a tool for LLM consumption.

    面向 LLM 的工具描述：name/description/parameters 会进入系统提示词，告诉模型
    「有哪些工具、各自能干什么、参数长什么样」。access_groups 是权限元数据，不发给
    模型，只在 ToolRegistry.get_schemas 里用于按用户分组过滤。
    """

    # 工具名
    name: str = Field(description="Tool name")
    # 工具职责描述（进入 prompt，直接影响模型如何正确使用该工具）
    description: str = Field(description="What this tool does")
    # 参数的 JSON Schema，由 get_args_schema() 的 Pydantic 模型生成
    parameters: Dict[str, Any] = Field(description="JSON Schema of parameters")
    # 允许访问的分组；空列表表示所有用户可见
    access_groups: List[str] = Field(
        default_factory=list, description="Groups permitted to access this tool"
    )
    # 参数 schema 是否已转换为严格模式（additionalProperties=false + 全字段 required）
    strict: bool = Field(
        default=False, description="Whether parameters is a strict-mode JSON schema"
    )


class ToolRejection(BaseModel):
    """Indicates tool execution should be rejected with a message.

    Used by transform_args to reject tool execution when arguments
    cannot be appropriately transformed for the user's context.

    由 ToolRegistry.transform_args 返回，用于「行级安全」拒绝：当无法把参数安全地
    改写为符合当前用户数据权限的形态时，返回本对象以拒绝执行，而非抛异常。
    """

    # 拒绝原因，会作为失败结果回喂 LLM 或写入审计日志
    reason: str = Field(
        description="Explanation of why the tool execution was rejected"
    )
