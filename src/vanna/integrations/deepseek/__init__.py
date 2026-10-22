"""
DeepSeek integration.

DeepSeek exposes an OpenAI-compatible Chat Completions API, so this module
reuses :class:`OpenAILlmService` and only fills in DeepSeek-specific defaults.
"""

from .llm import DEFAULT_BASE_URL, DeepSeekLlmService

__all__ = ["DeepSeekLlmService", "DEFAULT_BASE_URL"]
