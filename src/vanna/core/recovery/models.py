"""
Recovery action models for error handling.

本模块定义错误恢复相关的数据模型：恢复动作类型枚举与恢复动作。接线情况：
handle_llm_error 已在 agent.py 的 LLM 调用层被消费（RETRY 退避重试、FALLBACK 降级、
SKIP 跳过），handle_tool_error 保持未调用、工具错误回喂 LLM。
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RecoveryActionType(str, Enum):
    """Types of recovery actions.

    恢复动作类型：重试 / 直接失败 / 降级回退 / 跳过。
    """

    # 重试当前操作
    RETRY = "retry"
    # 直接失败并终止
    FAIL = "fail"
    # 用备用方案/降级值继续
    FALLBACK = "fallback"
    # 跳过当前操作继续流程
    SKIP = "skip"


class RecoveryAction(BaseModel):
    """Action to take when recovering from an error.

    一次错误恢复的动作指令：指定动作类型，以及重试延迟、降级值、附带消息等可选参数。
    """

    # 动作类型
    action: RecoveryActionType = Field(description="Type of recovery action")
    # 重试前的等待时间（毫秒），仅 RETRY 时有效
    retry_delay_ms: Optional[int] = Field(
        default=None, description="Delay before retry in milliseconds"
    )
    # 降级时使用的回退值，仅 FALLBACK 时有效
    fallback_value: Optional[Any] = Field(
        default=None, description="Fallback value to use"
    )
    # 附带说明（如失败原因）
    message: Optional[str] = Field(
        default=None, description="Message to include with action"
    )
