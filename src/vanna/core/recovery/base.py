"""
Base error recovery strategy interface.

Recovery strategies allow you to customize how the agent handles errors
during tool execution and LLM communication.

本模块定义错误恢复策略接口，用于自定义工具执行与 LLM 通信出错时的重试/降级行为。
接线情况：
- handle_llm_error 已在 agent.py 的 LLM 调用层（_invoke_llm_with_recovery）被调用，
  对瞬时故障（429/5xx/超时等）做退避重试/降级；
- handle_tool_error 已在 agent.py 的 _recover_tool_result 接线，但**仅对未捕获异常**
  生效（ToolResult.metadata 带 exception_type）。SQL 语法错误等业务错误仍按 agentic
  范式转成 ToolResult(success=False) 回喂 LLM 自我修复，避免与 LLM 重试叠加。
"""

from abc import ABC
from typing import TYPE_CHECKING

from .models import RecoveryAction, RecoveryActionType

if TYPE_CHECKING:
    from ..tool.models import ToolContext
    from ..llm import LlmRequest


class ErrorRecoveryStrategy(ABC):
    """Strategy for handling errors and implementing retry logic.

    Subclass this to create custom error recovery strategies that can:
    - Retry failed operations with backoff
    - Fallback to alternative approaches
    - Log errors to external systems
    - Gracefully degrade functionality

    Example:
        class ExponentialBackoffStrategy(ErrorRecoveryStrategy):
            async def handle_tool_error(
                self, error: Exception, context: ToolContext, attempt: int
            ) -> RecoveryAction:
                if attempt < 3:
                    delay = (2 ** attempt) * 1000  # Exponential backoff
                    return RecoveryAction(
                        action=RecoveryActionType.RETRY,
                        retry_delay_ms=delay,
                        message=f"Retrying after {delay}ms"
                    )
                return RecoveryAction(
                    action=RecoveryActionType.FAIL,
                    message="Max retries exceeded"
                )

        agent = AgentRunner(
            llm_service=...,
            error_recovery_strategy=ExponentialBackoffStrategy()
        )

    错误恢复策略的抽象基类。两个方法均默认返回「立即失败」，子类可返回 RETRY /
    FALLBACK 等动作实现退避重试或降级。其中 handle_llm_error 在 LLM 调用层接线；
    handle_tool_error 在工具执行层接线，但仅对「未捕获异常」生效（业务错误仍回喂
    LLM 自我修复）。
    """

    async def handle_tool_error(
        self, error: Exception, context: "ToolContext", attempt: int = 1
    ) -> RecoveryAction:
        """Handle errors during tool execution.

        Args:
            error: The exception that occurred
            context: Tool execution context
            attempt: Current attempt number (1-indexed)

        Returns:
            RecoveryAction indicating how to proceed

        处理工具执行错误；attempt 为当前第几次尝试（从 1 起），可据此实现退避。
        """
        # Default: fail immediately
        # 默认：立即失败（不重试）
        return RecoveryAction(
            action=RecoveryActionType.FAIL, message=f"Tool error: {str(error)}"
        )

    async def handle_llm_error(
        self, error: Exception, request: "LlmRequest", attempt: int = 1
    ) -> RecoveryAction:
        """Handle errors during LLM communication.

        Args:
            error: The exception that occurred
            request: The LLM request that failed
            attempt: Current attempt number (1-indexed)

        Returns:
            RecoveryAction indicating how to proceed

        处理 LLM 通信错误（超时/限流/鉴权失败等），返回恢复动作。
        """
        # Default: fail immediately
        # 默认：立即失败（不重试）
        return RecoveryAction(
            action=RecoveryActionType.FAIL, message=f"LLM error: {str(error)}"
        )
