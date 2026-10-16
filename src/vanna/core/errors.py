"""
Exception classes for the Vanna Agents framework.

This module defines all custom exceptions used throughout the framework.

本模块定义了框架在整个 Text2SQL 链路中可能用到的语义异常类型。
所有自定义异常都继承自 AgentError，目的是让调用方（尤其是自定义
LifecycleHook、Tool、Middleware 的开发者）能够用统一的基类一次性捕获
"框架自身产生的错误"，与 Python 内置异常 / 第三方库异常区分开来。

设计意图说明（重要，避免误用）：
- 当前核心执行路径（Agent._send_message / ToolRegistry.execute）并不
  主动抛出这些异常，而是用 ToolResult(success=False, error=...) 来
  传递工具执行失败，再由 LLM 读取错误信息自我修复。
- 因此本异常体系更多是"预留的语义出口"，供自定义扩展点（hook、tool、
  enricher 等）在需要显式中断流程时主动抛出，或供框架未来演进使用。
  【待确认设计】
"""


class AgentError(Exception):
    """Base exception for agent framework.

    框架所有自定义异常的基类，用于统一标识框架内部错误，便于上层（如自定义
    服务器、外部调用方）通过捕获 AgentError 一次性兜底所有框架级异常，而无需
    逐一列举子类。

    适用场景：
    - 在自定义 hook / 中间件中需要抛出框架级错误时，优先继承或使用它；
    - 在顶层做防御性捕获时，用 except AgentError 作为安全网。
    """

    pass


class ToolExecutionError(AgentError):
    """Error during tool execution.

    工具执行过程中发生错误的语义异常，表示某个工具（如 run_sql、visualize_data）
    在执行阶段失败。注意：框架内部的 ToolRegistry.execute 已用 try/except 将工具
    异常转换为 ToolResult(success=False)，因此本异常更适用于工具作者在希望
    "显式中断并向上抛出"的场景主动 raise。  【待确认设计】
    """

    pass


class ToolNotFoundError(AgentError):
    """Tool not found in registry.

    请求的工具在注册表中不存在的语义异常。业务含义：LLM 幻觉出一个未注册的
    工具名，或代码拼写错误导致查找失败。注意：ToolRegistry.execute 对"工具不存在"
    目前是返回失败结果而非抛出本异常，因此它更像语义占位。  【待确认设计】
    """

    pass


class PermissionError(AgentError):
    """User lacks required permissions.

    用户缺少所需权限的语义异常。业务含义：用户的分组（group_memberships）与
    工具/功能的访问组（access_groups）无交集，被拒绝访问。

    命名冲突提醒：本类与 Python 内置的 PermissionError 同名。框架内实际抛出/捕获
    的"文件/系统权限错误"（如 tools/file_system.py）用的是内置 PermissionError，
    而非本类。使用时需显式 import 本类以避免混淆。  【待确认设计】
    """

    pass


class ConversationNotFoundError(AgentError):
    """Conversation not found.

    会话不存在时的语义异常。业务含义：按 conversation_id 检索会话时未命中
    （可能被删除或 id 错误）。适用场景：自定义 ConversationStore 或工具在
    会话缺失时抛出。
    """

    pass


class LlmServiceError(AgentError):
    """Error communicating with LLM service.

    与 LLM 服务通信失败的语义异常。业务含义：模型服务超时、鉴权失败、限流、
    返回非法响应等底层通信问题。适用场景：自定义 LlmService 实现或 LLM 中间件
    在调用失败时抛出。
    """

    pass


class ValidationError(AgentError):
    """Data validation error.

    数据校验失败的语义异常。业务含义：输入数据（如工具参数、SQL 文本）未通过
    校验。命名冲突提醒：pydantic 也定义了 ValidationError，两者同名不同源；
    本类用于框架自身的业务校验错误，pydantic 的用于模型字段校验。  【待确认设计】
    """

    pass
