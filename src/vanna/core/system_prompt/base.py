"""
System prompt builder interface.

This module contains the abstract base class for system prompt builders.

本模块定义系统提示词构建器的抽象基类，是「提示词策略」的插件化扩展点：不同业务
只需实现 build_system_prompt，即可自定义角色设定、工具清单、领域指令等提示词内容。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..tool.models import ToolSchema
    from ..user.models import User


class SystemPromptBuilder(ABC):
    """Abstract base class for system prompt builders.

    Subclasses should implement the build_system_prompt method to generate
    system prompts based on user context and available tools.

    系统提示词构建器的抽象基类。Agent 在每轮对话开始时调用它，入参是当前用户与
    其可见工具，返回的提示词会与 LlmContextEnhancer 的输出合并后送入 LLM。
    """

    @abstractmethod
    async def build_system_prompt(
        self, user: "User", tools: List["ToolSchema"]
    ) -> Optional[str]:
        """
        Build a system prompt based on user context and available tools.

        Args:
            user: The user making the request
            tools: List of tools available to the user

        Returns:
            System prompt string, or None if no system prompt should be used

        基于用户与其可用工具构造系统提示词；返回 None 表示不使用系统提示词。
        """
        pass
