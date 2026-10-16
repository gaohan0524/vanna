"""
Base classes for rich UI components.

This module provides the base RichComponent class and supporting enums
for the component system.

本模块提供富 UI 组件体系的基类与枚举。RichComponent 是「命令式 UI」的实现基础：
Agent 不保存任何前端状态，而是通过（id + lifecycle）让前端做增量更新；本模块的
update() 与 serialize_for_frontend() 分别实现了「生成更新」与「规范线上格式」。
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, TypeVar

from pydantic import BaseModel, Field

# Type variable for self-returning methods
T = TypeVar("T", bound="RichComponent")


class ComponentType(str, Enum):
    """Types of rich UI components.

    富组件类型枚举：涵盖基础、原语、交互、数据、状态、产物与 UI 状态更新等类别。
    前端据此决定渲染方式。
    """

    # Basic components
    TEXT = "text"
    CARD = "card"
    CONTAINER = "container"

    # Primitive UI components (domain-agnostic)
    STATUS_CARD = "status_card"
    PROGRESS_DISPLAY = "progress_display"
    LOG_VIEWER = "log_viewer"
    BADGE = "badge"
    ICON_TEXT = "icon_text"

    # Interactive components
    TASK_LIST = "task_list"
    PROGRESS_BAR = "progress_bar"
    BUTTON = "button"
    BUTTON_GROUP = "button_group"

    # Data components
    TABLE = "table"
    DATAFRAME = "dataframe"
    CHART = "chart"
    CODE_BLOCK = "code_block"

    # Status components
    STATUS_INDICATOR = "status_indicator"
    NOTIFICATION = "notification"
    ALERT = "alert"

    # Artifact components
    ARTIFACT = "artifact"

    # UI state components
    STATUS_BAR_UPDATE = "status_bar_update"
    TASK_TRACKER_UPDATE = "task_tracker_update"
    CHAT_INPUT_UPDATE = "chat_input_update"

    # Legacy (deprecated - use primitives instead)
    TOOL_EXECUTION = "tool_execution"


class ComponentLifecycle(str, Enum):
    """Component lifecycle operations.

    组件生命周期：创建 / 更新 / 替换 / 移除。前端靠（id, lifecycle）做增量渲染，
    这是无状态后端实现「命令式 UI」的关键。
    """

    CREATE = "create"
    UPDATE = "update"
    REPLACE = "replace"
    REMOVE = "remove"


class RichComponent(BaseModel):
    """Base class for all rich UI components.

    所有富 UI 组件的基类。id 是前端定位节点的唯一键；update() 保留 id 并把 lifecycle
    改为 UPDATE，从而让前端对同一节点做增量更新而非重建。
    """

    # 组件唯一 ID，前端据此定位/更新节点
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # 组件类型
    type: ComponentType
    # 生命周期（create/update/replace/remove）
    lifecycle: ComponentLifecycle = ComponentLifecycle.CREATE
    # 组件专属数据载荷（serialize_for_frontend 会把子类字段归到这里）
    data: Dict[str, Any] = Field(default_factory=dict)
    # 子组件 ID 列表
    children: List[str] = Field(default_factory=list)  # Child component IDs
    # 时间戳
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    # 是否可见
    visible: bool = True
    # 是否可交互
    interactive: bool = False

    def update(self: T, **kwargs: Any) -> T:
        """Create an updated copy of this component.

        生成「更新」副本：保留 id，把 lifecycle 改为 UPDATE 并刷新时间戳。
        前端收到同 id + UPDATE 时会对原节点做增量更新，实现状态卡/任务列表的实时刷新。
        """
        updated_data = self.model_dump()
        updated_data.update(kwargs)
        updated_data["lifecycle"] = ComponentLifecycle.UPDATE
        updated_data["timestamp"] = datetime.utcnow().isoformat()
        return self.__class__(**updated_data)

    def hide(self: T) -> T:
        """Create a hidden copy of this component.

        生成隐藏副本（visible=False），用于条件隐藏组件而不移除节点。
        """
        return self.update(visible=False)

    def show(self: T) -> T:
        """Create a visible copy of this component.

        生成可见副本（visible=True），与 hide 成对使用。
        """
        return self.update(visible=True)

    def serialize_for_frontend(self) -> Dict[str, Any]:
        """Normalize component payload for the frontend renderer.

        The frontend expects component-specific fields to live under the
        ``data`` key while the shared metadata (``id``, ``type``, layout hints,
        etc.) remains at the top level. Pydantic's ``model_dump`` keeps
        component attributes at the top level, so we remap them here before
        streaming them across the wire.

        规范线上格式（前后端 wire 契约）：把组件专属字段收拢到 data 下，共享元数据
        （id/type/lifecycle/children/timestamp/visible/interactive）留在顶层，并把
        枚举转成字符串。前端解析时即按此约定读取。
        """

        # Base fields that should remain at the top level of the payload.
        shared_fields = {
            "id",
            "type",
            "lifecycle",
            "children",
            "timestamp",
            "visible",
            "interactive",
        }

        raw = self.model_dump()
        payload: Dict[str, Any] = {}

        # Preserve any existing data payload so implementations can opt-in to
        # advanced usage without losing information.
        raw_data = raw.get("data")
        if raw_data is not None and isinstance(raw_data, dict):
            component_data: Dict[str, Any] = raw_data.copy()
        else:
            # Handle case where data might be a sequence or other type, or None
            component_data = {}

        for key, value in raw.items():
            if key in shared_fields:
                payload[key] = value
            elif key == "data":
                # For most components, skip the base data field
                continue
            elif (
                key == "rows"
                and hasattr(self, "type")
                and self.type.value == "dataframe"
            ):
                # For DataFrame components, the 'rows' field contains the actual row data
                # which should be included in the component_data as 'data' for the frontend
                # 特例：dataframe 组件的 rows 字段被重命名为 data 供前端读取表格行
                component_data["data"] = value
            else:
                component_data[key] = value

        payload["data"] = component_data

        # Ensure enums are serialized as primitive values for the frontend.
        payload["type"] = self.type.value
        payload["lifecycle"] = self.lifecycle.value

        return payload
