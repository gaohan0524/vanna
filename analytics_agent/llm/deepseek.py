"""DeepSeek adapter.

DeepSeek 提供 OpenAI 兼容的 `/chat/completions` 接口，因此直接复用
`OpenAILlmService`，只需补一件事：**把 `LlmRequest.temperature` 透传下去**。

框架的 `OpenAILlmService._build_payload()` 目前不透传 temperature，
而 Text2SQL 需要固定 `temperature=0` 才能保证同一问题重复评测结果可比。
这里用子类覆盖，避免改动框架对其它供应商的既有行为。
"""

from __future__ import annotations

from typing import Any, Dict

from vanna.core.llm import LlmRequest
from vanna.integrations.openai import OpenAILlmService

DEFAULT_BASE_URL = "https://api.deepseek.com"
SUPPORTED_MODELS = ("deepseek-chat", "deepseek-reasoner")


class DeepSeekLlmService(OpenAILlmService):
    """OpenAI-compatible client for DeepSeek with temperature pass-through."""

    def __init__(
        self,
        model: str = "deepseek-chat",
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        **kwargs: Any,
    ) -> None:
        if not api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set; add it to .env or pass api_key explicitly"
            )
        if not str(model).startswith("deepseek"):
            raise ValueError(
                f"DeepSeek model should look like {' or '.join(SUPPORTED_MODELS)}, got {model!r}"
            )
        super().__init__(model=model, api_key=api_key, base_url=base_url, **kwargs)

    def _build_payload(self, request: LlmRequest) -> Dict[str, Any]:
        payload = super()._build_payload(request)
        payload["temperature"] = request.temperature
        return payload
