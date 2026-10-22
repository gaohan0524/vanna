"""LLM service adapters that need behavior tweaks on top of the framework ones."""

from .deepseek import DeepSeekLlmService

__all__ = ["DeepSeekLlmService"]
