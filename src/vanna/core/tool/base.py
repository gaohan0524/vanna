"""
Tool domain interface.

This module contains the abstract base class for tools.

本模块定义工具的抽象基类 Tool，是框架「插件化工具」的唯一契约：任何工具只要继承
Tool 并实现 name / description / get_args_schema / execute 四个抽象成员，就能被
ToolRegistry 注册、被 Agent 在工具循环中调用，无需改动任何框架代码。
"""

from abc import ABC, abstractmethod
from typing import Generic, List, Type, TypeVar

from .models import ToolContext, ToolResult, ToolSchema

# Type variable for tool argument types
# 泛型参数 T 绑定「工具参数」的 Pydantic 模型类型，实现参数 schema 与执行签名的类型关联
T = TypeVar("T")


class Tool(ABC, Generic[T]):
    """Abstract base class for tools.

    工具的抽象基类。T 是参数 Pydantic 模型：get_args_schema 返回该模型的类，
    execute 收到的 args 即该模型的实例（已由 registry 完成校验）。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this tool.

        工具的唯一名称，会出现在系统提示词与 LLM 的 tool_calls 里，必须与注册名一致。
        """
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what this tool does.

        工具职责描述，直接进入 LLM 提示词，决定模型能否在正确场景调用它。
        """
        pass

    @property
    def access_groups(self) -> List[str]:
        """Groups permitted to access this tool.

        允许访问的分组列表；默认空列表表示所有用户可见。非空时 ToolRegistry 会用
        「用户分组 ∩ 工具分组」判定可见性（第一道权限闸门）。
        """
        return []

    @abstractmethod
    def get_args_schema(self) -> Type[T]:
        """Return the Pydantic model for arguments.

        返回参数 Pydantic 模型类，registry 用它对 LLM 参数做校验，并据此生成 JSON Schema。
        """
        pass

    @abstractmethod
    async def execute(self, context: ToolContext, args: T) -> ToolResult:
        """Execute the tool with validated arguments.

        Args:
            context: Execution context containing user, conversation_id, and request_id
            args: Validated tool arguments

        Returns:
            ToolResult with success status, result for LLM, and optional UI component

        真正执行工具逻辑的入口。args 已经过 Pydantic 校验，context 携带用户身份
        与记忆等句柄。Text2SQL 中，run_sql 工具就在这里执行 SQL 并返回结果表。
        """
        pass

    def get_schema(self, strict: bool = False) -> ToolSchema:
        """Generate tool schema for LLM.

        把 name/description/参数 JSON Schema/access_groups 组装成 ToolSchema，
        供 ToolRegistry.get_schemas 过滤后送入 LLM 提示词。

        Args:
            strict: 若为 True，把参数 schema 转成严格模式（``additionalProperties:
                false`` + 全字段 required），以满足 OpenAI Structured Outputs /
                strict function calling 等供应商要求，从而强制模型输出合法结构。
        """
        from typing import Any, cast

        from ..llm.schema import make_strict_json_schema

        args_model = self.get_args_schema()
        # Get the schema - args_model should be a Pydantic model class
        # 把 Pydantic 模型类转成 JSON Schema（函数式调用的参数定义）
        schema = (
            cast(Any, args_model).model_json_schema()
            if hasattr(args_model, "model_json_schema")
            else {}
        )
        if strict and schema:
            schema = make_strict_json_schema(schema)
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=schema,
            access_groups=self.access_groups,
            strict=strict,
        )
