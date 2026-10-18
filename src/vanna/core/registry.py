"""
Tool registry for the Vanna Agents framework.

This module provides the ToolRegistry class for managing and executing tools.

本模块提供工具注册表 ToolRegistry，是 Vanna 的「安全边界」所在：工具注册、按用户
分组过滤（第一道权限闸门）、参数校验、行级安全（transform_args）、审计与执行都在
这里完成。Agent 只负责编排，所有涉及权限与工具调用的安全逻辑都收敛到本模块。
"""

import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type, TypeVar, Union

from .tool import Tool, ToolCall, ToolContext, ToolRejection, ToolResult, ToolSchema
from .user import User

if TYPE_CHECKING:
    from .audit import AuditLogger
    from .agent.config import AuditConfig

T = TypeVar("T")


class _LocalToolWrapper(Tool[T]):
    """Wrapper for tools with configurable access groups.

    工具包装器：给原本「无权限限制」的工具动态套上 access_groups。注册时若指定了
    访问分组，就用本包装器覆盖 access_groups 属性，而 name/description/execute 等
    仍委托给原工具。
    """

    def __init__(self, wrapped_tool: Tool[T], access_groups: List[str]):
        self._wrapped_tool = wrapped_tool
        self._access_groups = access_groups

    @property
    def name(self) -> str:
        return self._wrapped_tool.name

    @property
    def description(self) -> str:
        return self._wrapped_tool.description

    @property
    def access_groups(self) -> List[str]:
        return self._access_groups

    def get_args_schema(self) -> Type[T]:
        return self._wrapped_tool.get_args_schema()

    async def execute(self, context: ToolContext, args: T) -> ToolResult:
        return await self._wrapped_tool.execute(context, args)


class ToolRegistry:
    """Registry for managing tools.

    工具注册表：持有 name → Tool 的映射，并提供「按用户过滤 schema」与「带完整安全
    链路的执行」两大能力。是工具可见性、行级安全与审计的唯一落地处。
    """

    def __init__(
        self,
        audit_logger: Optional["AuditLogger"] = None,
        audit_config: Optional["AuditConfig"] = None,
    ) -> None:
        self._tools: Dict[str, Tool[Any]] = {}
        self.audit_logger = audit_logger
        if audit_config is not None:
            self.audit_config = audit_config
        else:
            # 未提供审计配置时用默认值（延迟导入避免循环依赖）
            from .agent.config import AuditConfig

            self.audit_config = AuditConfig()

    def register_local_tool(self, tool: Tool[Any], access_groups: List[str]) -> None:
        """Register a local tool with optional access group restrictions.

        Args:
            tool: The tool to register
            access_groups: List of groups that can access this tool.
                          If None or empty, tool is accessible to all users.

        注册本地工具。同名重复注册抛 ValueError；access_groups 非空时用
        _LocalToolWrapper 包装以附加权限，为空则原样注册（对所有人可见）。
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")

        if access_groups:
            # Wrap the tool with access groups
            wrapped_tool = _LocalToolWrapper(tool, access_groups)
            self._tools[tool.name] = wrapped_tool
        else:
            # No access restrictions, register as-is
            self._tools[tool.name] = tool

    def register(
        self, tool: Tool[Any], access_groups: Optional[List[str]] = None
    ) -> None:
        """Register a tool (alias of ``register_local_tool``).

        早期示例/文档使用 ``registry.register(tool)``，本方法作为兼容入口保留，
        内部委托 ``register_local_tool``。access_groups 缺省为空（所有用户可见）。
        """
        self.register_local_tool(tool, list(access_groups or []))

    async def get_tool(self, name: str) -> Optional[Tool[Any]]:
        """Get a tool by name.

        按名称取工具；不存在返回 None。
        """
        return self._tools.get(name)

    async def list_tools(self) -> List[str]:
        """List all registered tool names.

        列出所有已注册工具名（不区分权限，供 workflow 等内部使用）。
        """
        return list(self._tools.keys())

    async def get_schemas(
        self, user: Optional[User] = None, *, strict: bool = False
    ) -> List[ToolSchema]:
        """Get schemas for all tools accessible to user.

        Args:
            user: If provided, only return tools the user can access.
            strict: If True, emit strict-mode JSON schemas (all fields required,
                ``additionalProperties: false``) for structured tool calling.

        返回用户可见工具 schema（第一道权限闸门）：user 为 None 时返回全部，
        否则逐个按分组交集过滤。LLM 只会拿到这里返回的工具清单。
        """
        schemas = []
        for tool in self._tools.values():
            if user is None or await self._validate_tool_permissions(tool, user):
                schemas.append(tool.get_schema(strict=strict))
        return schemas

    async def _validate_tool_permissions(self, tool: Tool[Any], user: User) -> bool:
        """Validate if user has access to tool based on group membership.

        Checks for intersection between user's group memberships and tool's access groups.
        If tool has no access groups specified, it's accessible to all users.

        分组交集判定：工具未设 access_groups 则人人可用；否则要求
        user.group_memberships 与 tool.access_groups 存在交集。
        """
        tool_access_groups = tool.access_groups
        if not tool_access_groups:
            return True

        user_groups = set(user.group_memberships)
        tool_groups = set(tool_access_groups)
        # Grant access if any group in user.group_memberships exists in tool.access_groups
        return bool(user_groups & tool_groups)

    async def transform_args(
        self,
        tool: Tool[T],
        args: T,
        user: User,
        context: ToolContext,
    ) -> Union[T, ToolRejection]:
        """Transform and validate tool arguments based on user context.

        This method allows per-user transformation of tool arguments, such as:
        - Applying row-level security (RLS) to SQL queries
        - Filtering available options based on user permissions
        - Validating required arguments are present
        - Redacting sensitive fields

        The default implementation performs no transformation (NoOp).
        Subclasses can override this method to implement custom transformation logic.

        Args:
            tool: The tool being executed
            args: Already Pydantic-validated arguments
            user: The user executing the tool
            context: Full execution context

        Returns:
            Either:
            - Transformed arguments (may be unchanged if no transformation needed)
            - ToolRejection with explanation of why args were rejected

        行级安全（RLS）钩子：在参数校验之后、执行之前，按用户改写参数（例如给 SQL
        强制注入租户过滤条件）。默认 NoOp 原样返回；返回 ToolRejection 即拒绝执行。
        这是实现「数据级安全」的核心扩展点。
        """
        return args  # Default: no transformation (NoOp)

    async def execute(
        self,
        tool_call: ToolCall,
        context: ToolContext,
    ) -> ToolResult:
        """Execute a tool call with validation.

        执行一次工具调用的完整安全链路：查工具 → 权限校验 → 参数校验 → transform_args
        （RLS）→ 审计（判定/调用/结果）→ 执行。全程异常兜底转 ToolResult(success=False)，
        不向上抛异常（供 LLM 读取错误自我修复）。
        """
        tool = await self.get_tool(tool_call.name)
        if not tool:
            # 工具不存在：返回失败结果而非抛异常，让 LLM 能看到并纠正
            msg = f"Tool '{tool_call.name}' not found"
            return ToolResult(
                success=False,
                result_for_llm=msg,
                ui_component=None,
                error=msg,
            )

        # Validate group access
        # 第二道权限闸门（执行时再验一次，防 LLM 幻觉出未在 schema 中的工具）
        if not await self._validate_tool_permissions(tool, context.user):
            msg = f"Insufficient group access for tool '{tool_call.name}'"

            # Audit access denial
            if (
                self.audit_logger
                and self.audit_config
                and self.audit_config.log_tool_access_checks
            ):
                await self.audit_logger.log_tool_access_check(
                    user=context.user,
                    tool_name=tool_call.name,
                    access_granted=False,
                    required_groups=tool.access_groups,
                    context=context,
                    reason=msg,
                )

            return ToolResult(
                success=False,
                result_for_llm=msg,
                ui_component=None,
                error=msg,
            )

        # Validate and parse arguments
        # 用工具的 Pydantic 参数模型校验 LLM 给的原始参数
        try:
            args_model = tool.get_args_schema()
            validated_args = args_model.model_validate(tool_call.arguments)
        except Exception as e:
            msg = f"Invalid arguments: {str(e)}"
            return ToolResult(
                success=False,
                result_for_llm=msg,
                ui_component=None,
                error=msg,
            )

        # Transform/validate arguments based on user context
        # 行级安全：按用户改写参数（默认 NoOp），返回 ToolRejection 则拒绝执行
        transform_result = await self.transform_args(
            tool=tool,
            args=validated_args,
            user=context.user,
            context=context,
        )

        if isinstance(transform_result, ToolRejection):
            return ToolResult(
                success=False,
                result_for_llm=transform_result.reason,
                ui_component=None,
                error=transform_result.reason,
            )

        # Use transformed arguments for execution
        final_args = transform_result

        # Audit successful access check
        if (
            self.audit_logger
            and self.audit_config
            and self.audit_config.log_tool_access_checks
        ):
            await self.audit_logger.log_tool_access_check(
                user=context.user,
                tool_name=tool_call.name,
                access_granted=True,
                required_groups=tool.access_groups,
                context=context,
            )

        # Audit tool invocation
        if (
            self.audit_logger
            and self.audit_config
            and self.audit_config.log_tool_invocations
        ):
            # Get UI features if available from context
            ui_features = context.metadata.get("ui_features_available", [])
            await self.audit_logger.log_tool_invocation(
                user=context.user,
                tool_call=tool_call,
                ui_features=ui_features,
                context=context,
                sanitize_parameters=self.audit_config.sanitize_tool_parameters,
            )

        # Execute tool with context-first signature
        try:
            # 真正执行工具（SQL 在这里跑），并统计耗时写入 metadata
            start_time = time.perf_counter()
            result = await tool.execute(context, final_args)
            execution_time_ms = (time.perf_counter() - start_time) * 1000

            # Add execution time to metadata
            result.metadata["execution_time_ms"] = execution_time_ms

            # Audit tool result
            if (
                self.audit_logger
                and self.audit_config
                and self.audit_config.log_tool_results
            ):
                await self.audit_logger.log_tool_result(
                    user=context.user,
                    tool_call=tool_call,
                    result=result,
                    context=context,
                )

            return result
        except Exception as e:
            # 执行异常兜底：转失败结果（含错误文本），供 LLM 读取后自我修复 SQL。
            # exception_type 标记「未捕获异常」来源，供 ErrorRecoveryStrategy 判断是否
            # 走基础设施级重试；业务错误（如 SQL 语法错误）不会带此标记。
            msg = f"Execution failed: {str(e)}"
            return ToolResult(
                success=False,
                result_for_llm=msg,
                ui_component=None,
                error=msg,
                metadata={"exception_type": type(e).__name__},
            )
