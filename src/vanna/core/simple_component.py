"""Base classes for simple UI components.

简化 UI 组件基类：面向「无富渲染能力」客户端的降级表示，仅支持文本/图片/链接
三类最基础的内容，保证任何终端（CLI、短信、极简网页）都能消费智能体的输出。
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from enum import Enum


class SimpleComponentType(str, Enum):
    """Types of simple components.

    简化组件类型：文本 / 图片 / 链接。
    """

    TEXT = "text"
    IMAGE = "image"
    LINK = "link"


class SimpleComponent(BaseModel):
    """A simple UI component with basic attributes.

    简化组件基类。它是 UiComponent.simple_component 的取值类型，与 RichComponent
    成对出现：同一结果在富客户端用 rich 渲染，在基础客户端用 simple 渲染。
    """

    # 组件类型（text/image/link）
    type: SimpleComponentType = Field(..., description="Type of the component.")
    # 语义类型（如 "sql"、"chart"），便于终端做分类处理
    semantic_type: Optional[str] = Field(
        default=None, description="Semantic type for better categorization."
    )
    # 附加元数据
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional metadata for the component."
    )

    def serialize_for_frontend(self) -> Dict[str, Any]:
        """Serialize simple component for API consumption.

        序列化为前端消费的字典（直接 model_dump，枚举会保留原始值形式）。
        """
        return self.model_dump()
