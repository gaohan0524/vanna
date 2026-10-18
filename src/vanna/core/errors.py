"""
Exception classes for the Vanna Agents framework.

本模块定义框架的语义异常层级。核心执行路径的约定：

- **工具执行失败不抛异常**：`ToolRegistry.execute` 统一返回
  ``ToolResult(success=False, error=...)``，由 LLM 读取错误后自我修复（agentic 范式）。
- **LLM 通信失败**：经 `ErrorRecoveryStrategy.handle_llm_error` 处理完毕后
  （返回 FAIL 或重试上限），由 `Agent._invoke_llm_with_recovery` 抛出
  ``LlmServiceError``，并保留原始异常为 ``__cause__``。
- **其余异常类属于扩展点语义**：供自定义 Tool / Hook / Store 在需要显式中断流程时
  主动抛出，或供上层用 ``except AgentError`` 统一兜底。

命名冲突提醒：``PermissionError`` / ``ValidationError`` 与 Python 内置 / Pydantic
同名。框架内部的系统权限错误仍用内置 ``PermissionError``，Pydantic 字段校验错误仍用
``pydantic.ValidationError``。使用本模块的同名类时请显式 import。
"""


class AgentError(Exception):
    """Base exception for all Vanna framework errors.

    框架所有自定义异常的基类。上层（自定义服务器、外部调用方）可以用
    ``except AgentError`` 一次性捕获框架级异常，与内置/第三方异常区分开。
    """

    pass


class ToolExecutionError(AgentError):
    """Error during tool execution (extension-point semantics).

    工具作者在需要「显式中断并向上抛出」时使用。注意：核心执行路径不会抛出它——
    `ToolRegistry.execute` 已把工具异常转换为 ``ToolResult(success=False)``，交给
    LLM 自我修复。若希望基础设施级异常触发重试，请让工具抛出普通异常并配置
    `ErrorRecoveryStrategy.handle_tool_error`。
    """

    pass


class ToolNotFoundError(AgentError):
    """Tool not found in registry (extension-point semantics).

    请求的工具不存在。核心执行路径不会抛出它：`ToolRegistry.execute` 返回失败结果，
    让 LLM 看到并纠正工具名。
    """

    pass


class PermissionError(AgentError):
    """User lacks required permissions (extension-point semantics).

    与 Python 内置 ``PermissionError`` 同名。框架内部的系统权限错误（如文件系统越权）
    仍使用内置 ``PermissionError``；本类供扩展点在业务权限校验失败时显式抛出。
    """

    pass


class ConversationNotFoundError(AgentError):
    """Conversation not found (extension-point semantics).

    按 conversation_id 检索会话未命中。适用于自定义 `ConversationStore` 或工具在
    会话缺失时显式抛出。
    """

    pass


class LlmServiceError(AgentError):
    """Error communicating with LLM service.

    与 LLM 服务通信失败的语义异常。由 `Agent._invoke_llm_with_recovery` 在错误恢复
    策略返回 FAIL、或重试次数超过防御上限时抛出；原始异常保存在 ``__cause__``。
    调用方可用 ``except LlmServiceError`` 或 ``except AgentError`` 捕获。
    """

    pass


class ValidationError(AgentError):
    """Data validation error (extension-point semantics).

    与 ``pydantic.ValidationError`` 同名。本类用于框架自身的业务校验错误（如自定义
    SQL/输入校验）；Pydantic 模型字段校验仍抛 Pydantic 的 ``ValidationError``。
    """

    pass
