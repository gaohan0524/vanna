"""
Default LLM context enhancer implementation using AgentMemory.

This implementation enriches the system prompt with relevant memories
based on the user's initial message.

本模块提供默认的 LLM 上下文增强器实现：基于用户首条消息检索 AgentMemory 中的
文本记忆，并把相关记忆片段拼接到系统提示词末尾。它是 Vanna「越用越准」的 RAG
落点——模型能读到既往积累的领域知识。
"""

from typing import TYPE_CHECKING, List, Optional
from .base import LlmContextEnhancer

if TYPE_CHECKING:
    from ..user.models import User
    from ..llm.models import LlmMessage
    from ...capabilities.agent_memory import AgentMemory, TextMemorySearchResult


class DefaultLlmContextEnhancer(LlmContextEnhancer):
    """Default enhancer that uses AgentMemory to add relevant context.

    This enhancer searches the agent's memory for relevant examples and
    tool use patterns based on the user's message, and adds them to the
    system prompt.

    Example:
        agent = Agent(
            llm_service=...,
            agent_memory=agent_memory,
            llm_context_enhancer=DefaultLlmContextEnhancer(agent_memory)
        )

    默认增强器：用用户问题去 AgentMemory.search_text_memories 检索相关文本记忆，
    拼成 "Relevant Context from Memory" 段落追加到系统提示词末尾。
    """

    def __init__(self, agent_memory: Optional["AgentMemory"] = None):
        """Initialize with optional agent memory.

        Args:
            agent_memory: Optional AgentMemory instance. If not provided,
                         enhancement will be skipped.
        """
        self.agent_memory = agent_memory

    async def enhance_system_prompt(
        self, system_prompt: str, user_message: str, user: "User"
    ) -> str:
        """Enhance system prompt with relevant memories.

        Searches agent memory for relevant text memories based on the
        user's message and adds them to the system prompt.

        Args:
            system_prompt: The original system prompt
            user_message: The initial user message
            user: The user making the request

        Returns:
            Enhanced system prompt with relevant examples from memory
        """
        if not self.agent_memory:
            return system_prompt

        try:
            # Import here to avoid circular dependency
            from ..tool import ToolContext
            import uuid

            # Create a temporary context for memory search
            # 构造临时 ToolContext 仅为满足记忆检索的接口签名（conversation_id 用 "temp" 占位）
            context = ToolContext(
                user=user,
                conversation_id="temp",
                request_id=str(uuid.uuid4()),
                agent_memory=self.agent_memory,
            )

            # Search for relevant text memories based on user message
            # 用用户问题做相似检索，取 top-5 文本记忆
            memories: List[
                "TextMemorySearchResult"
            ] = await self.agent_memory.search_text_memories(
                query=user_message, context=context, limit=5
            )

            if not memories:
                return system_prompt

            # Format memories as context snippets to add to system prompt
            examples_section = "\n\n## Relevant Context from Memory\n\n"
            examples_section += "The following domain knowledge and context from prior interactions may be relevant:\n\n"

            for result in memories:
                memory = result.memory
                examples_section += f"• {memory.content}\n"

            # Append examples to system prompt
            return system_prompt + examples_section

        except Exception as e:
            # If memory search fails, return original prompt
            # Don't fail the entire request due to memory issues
            # 记忆检索失败不阻断主链路：原样返回提示词，仅记一条 warning
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to enhance system prompt with memories: {e}")
            return system_prompt

    async def enhance_user_messages(
        self, messages: list["LlmMessage"], user: "User"
    ) -> list["LlmMessage"]:
        """Enhance user messages.

        The default implementation doesn't modify user messages.
        Override this to add context to user messages if needed.

        Args:
            messages: The list of messages
            user: The user making the request

        Returns:
            Original list of messages (unmodified)

        默认不修改用户消息（增强只作用于系统提示词），保持最小干预。
        """
        return messages
