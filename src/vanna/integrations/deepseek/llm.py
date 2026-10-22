"""DeepSeek LLM service.

DeepSeek 提供 OpenAI 兼容的 `/chat/completions` 接口，因此这里只做三件事：

1. 把 base_url 默认到 ``https://api.deepseek.com``；
2. 从 ``DEEPSEEK_API_KEY`` 读取密钥（也可显式传入）；
3. 默认 ``temperature=0.0``——Text2SQL 需要确定性输出。

工具调用（tools / tool_calls）与消息格式完全复用 ``OpenAILlmService``，
不再覆盖其私有方法，避免框架升级时失效。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..openai import OpenAILlmService

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekLlmService(OpenAILlmService):
    """DeepSeek-backed LLM service via the OpenAI-compatible API.

    Args:
        model: DeepSeek model id (e.g. ``deepseek-chat``); any id accepted by
            the provider works, this class does not whitelist names.
        api_key: API key; falls back to env ``DEEPSEEK_API_KEY``.
        base_url: API base URL; falls back to env ``DEEPSEEK_BASE_URL``.
        temperature: Sampling temperature. Defaults to ``0.0`` because SQL
            generation should be deterministic. Pass ``None`` to omit the
            parameter entirely (useful for models that reject it).
        **kwargs: Extra kwargs forwarded to :class:`OpenAILlmService`
            (e.g. ``timeout``，``max_retries``).

    Example:
        >>> from vanna.integrations.deepseek import DeepSeekLlmService
        >>> llm = DeepSeekLlmService(model="deepseek-chat", api_key="sk-...")
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: Optional[float] = 0.0,
        **kwargs: Any,
    ) -> None:
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set; add it to the environment or pass api_key explicitly"
            )

        # Ollama 标签（如 qwen2.5:7b）几乎必然是从本地配置复制过来的
        if ":" in str(model):
            raise ValueError(
                f"model={model!r} looks like an Ollama tag; use a DeepSeek API model id "
                f"(e.g. {DEFAULT_MODEL})"
            )

        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            temperature=temperature,
            **kwargs,
        )
