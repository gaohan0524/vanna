"""
LLM domain models.

This module contains data models for LLM communication.

本模块定义与大模型通信的全部数据契约：消息（LlmMessage）、请求（LlmRequest）、
响应（LlmResponse）与流式分片（LlmStreamChunk）。它们是 Agent 编排层与具体 LlmService
供应商实现之间的中立接口，使框架不依赖任何一家模型厂商的协议细节。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..tool.models import ToolCall
from ..user.models import User


class LlmMessage(BaseModel):
    """Message format for LLM communication.

    单条会话消息的通用表示，屏蔽不同厂商（OpenAI/Anthropic/…）的消息格式差异。
    role 取值约定为 system / user / assistant / tool。
    """

    # 消息角色（system/user/assistant/tool）
    role: str = Field(description="Message role")
    # 消息正文
    content: str = Field(description="Message content")
    # assistant 角色携带的工具调用；tool 角色则为空
    tool_calls: Optional[List[ToolCall]] = Field(default=None)
    # tool 角色消息必填：用于把工具结果与对应的 assistant tool_call 配对
    tool_call_id: Optional[str] = Field(default=None)


class LlmRequest(BaseModel):
    """Request to LLM service.

    一次完整 LLM 调用的输入封装：上下文消息 + 可用工具 + 采样参数 + 系统提示词。
    由 Agent._build_llm_request 在工具循环的每一轮重建。
    """

    # 发往模型的完整消息序列（含历史与最新的工具结果）
    messages: List[LlmMessage] = Field(description="Messages to send")
    # 可用工具列表（实为 ToolSchema，用 Any 避免循环导入）
    tools: Optional[List[Any]] = Field(
        default=None, description="Available tools"
    )  # Will be ToolSchema but avoiding circular import
    # 发起请求的用户，供供应商做限流、审计或个性化
    user: User = Field(description="User making the request")
    # 是否流式返回
    stream: bool = Field(default=False, description="Whether to stream response")
    # 采样温度，控制输出的随机性
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # 最大生成 token 数；None 表示不限制
    max_tokens: Optional[int] = Field(default=None, gt=0)
    # 系统提示词（角色设定 + 工具清单 + 记忆工作流指令）
    system_prompt: Optional[str] = Field(
        default=None, description="System prompt for the LLM"
    )
    # 附加元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LlmResponse(BaseModel):
    """Response from LLM.

    一次完整 LLM 调用的输出。Agent 依据 is_tool_call() 决定进入「继续调用工具」分支
    还是「输出最终答案」分支——这是工具循环的判定核心。
    """

    # 模型生成的文本答案（调用工具时可能为空）
    content: Optional[str] = None
    # 模型发起的工具调用（Text2SQL 中最典型的是 run_sql）
    tool_calls: Optional[List[ToolCall]] = None
    # 结束原因（stop / tool_calls / length 等），用于判断是否被截断
    finish_reason: Optional[str] = None
    # token 用量统计（prompt/completion tokens），用于成本核算
    usage: Optional[Dict[str, int]] = None
    # 附加元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_tool_call(self) -> bool:
        """Check if this response contains tool calls.

        判断本次响应是否要求调用工具；返回 True 时 Agent 继续工具循环，False 时视为
        最终答案并结束循环。
        """
        return self.tool_calls is not None and len(self.tool_calls) > 0


class LlmStreamChunk(BaseModel):
    """Streaming chunk from LLM.

    流式响应的单个分片。当前框架（Agent._handle_streaming_response）会把这些分片
    聚合成一个完整 LlmResponse 后再处理，因此它更接近「传输单元」而非「展示单元」。
    """

    # 本分片的增量文本
    content: Optional[str] = None
    # 本分片携带的工具调用增量（部分模型会在流中逐块给出）
    tool_calls: Optional[List[ToolCall]] = None
    # 结束原因
    finish_reason: Optional[str] = None
    # 附加元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)
