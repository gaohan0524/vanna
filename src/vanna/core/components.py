"""
UI component base class.

This module defines the UiComponent class which is the return type for tool executions.
It's placed in core/ because it's a fundamental type that tools return, not just a UI concern.

本模块定义 UiComponent——工具执行的返回类型，也是整条链路「组件级流式」的传输单元。
它之所以放在 core/（而非 components/）是因为它是工具返回的基础类型，属于核心契约，
而非单纯的 UI 关注点。UiComponent 同时承载「富组件」（rich，必填）与「简化组件」
（simple，可选降级），保证同一份结果能在不同能力的前端上渲染。
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class UiComponent(BaseModel):
    """Base class for UI components streamed to client.

    This wraps both rich and simple component representations,
    allowing tools to return structured UI updates.

    Note: We use Any for component types to avoid circular dependencies.
    Type validation happens at runtime through validators.

    流式推送给客户端的组件统一载体：rich_component 面向富客户端（表格/图表/状态卡），
    simple_component 面向基础客户端（仅文本/图片/链接）。字段类型声明为 Any 是为了
    规避循环导入，真实类型在 model_validator 里运行时校验。
    """

    # 分片时间戳
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    # 富组件（必填），如 DataFrameComponent / StatusCardComponent
    rich_component: Any = Field(
        ..., description="Rich component for advanced rendering"
    )
    # 简化组件（可选），如 SimpleTextComponent，用于降级渲染
    simple_component: Optional[Any] = Field(
        None, description="Simple component for basic rendering"
    )

    @model_validator(mode="after")
    def validate_components(self) -> "UiComponent":
        """Validate that components are the correct types at runtime.

        运行时校验组件真实类型：因字段声明为 Any（规避循环导入），此处用
        isinstance 兜底，确保 rich 必为 RichComponent、simple 为 SimpleComponent 或 None。
        """
        # Import from core - clean imports, no circular dependency
        from .rich_component import RichComponent
        from .simple_component import SimpleComponent

        if not isinstance(self.rich_component, RichComponent):
            raise ValueError(
                f"rich_component must be a RichComponent, got {type(self.rich_component)}"
            )

        if self.simple_component is not None and not isinstance(
            self.simple_component, SimpleComponent
        ):
            raise ValueError(
                f"simple_component must be a SimpleComponent or None, got {type(self.simple_component)}"
            )

        return self

    model_config = {"arbitrary_types_allowed": True}
