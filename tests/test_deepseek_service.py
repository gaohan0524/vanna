"""Unit tests for the DeepSeek adapter (no network, no API key required)."""

from __future__ import annotations

import pytest

pytest.importorskip("openai")

from vanna.core.llm import LlmMessage, LlmRequest
from vanna.core.user import User

from analytics_agent.llm.deepseek import DeepSeekLlmService


def _request(temperature: float = 0.0) -> LlmRequest:
    return LlmRequest(
        messages=[LlmMessage(role="user", content="hi")],
        user=User(id="u"),
        temperature=temperature,
    )


def test_temperature_is_forwarded():
    service = DeepSeekLlmService(model="deepseek-chat", api_key="sk-test")

    payload = service._build_payload(_request(0.0))

    assert payload["temperature"] == 0.0
    assert payload["model"] == "deepseek-chat"


def test_base_url_defaults_to_deepseek():
    service = DeepSeekLlmService(model="deepseek-chat", api_key="sk-test")

    assert str(service._client.base_url).rstrip("/") == "https://api.deepseek.com"


def test_rejects_non_deepseek_model():
    with pytest.raises(ValueError, match="deepseek"):
        DeepSeekLlmService(model="qwen2.5:7b", api_key="sk-test")


def test_rejects_missing_api_key():
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekLlmService(model="deepseek-chat", api_key="")
