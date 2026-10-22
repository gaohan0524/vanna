"""Unit tests for the DeepSeek integration and OpenAI temperature opt-in.

No network and no real API key are required.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openai")

from vanna.core.llm import LlmMessage, LlmRequest
from vanna.core.user import User
from vanna.integrations.deepseek import DeepSeekLlmService
from vanna.integrations.openai import OpenAILlmService


def _request() -> LlmRequest:
    return LlmRequest(
        messages=[LlmMessage(role="user", content="hi")],
        user=User(id="u"),
        temperature=0.0,
    )


def test_openai_omits_temperature_by_default():
    service = OpenAILlmService(model="gpt-test", api_key="sk-test")

    payload = service._build_payload(_request())

    assert "temperature" not in payload


def test_openai_sends_temperature_when_configured():
    service = OpenAILlmService(model="gpt-test", api_key="sk-test", temperature=0.0)

    payload = service._build_payload(_request())

    assert payload["temperature"] == 0.0


def test_deepseek_defaults_to_zero_temperature():
    service = DeepSeekLlmService(model="deepseek-chat", api_key="sk-test")

    payload = service._build_payload(_request())

    assert payload["temperature"] == 0.0
    assert payload["model"] == "deepseek-chat"
    assert str(service._client.base_url).rstrip("/") == "https://api.deepseek.com"


def test_deepseek_accepts_custom_model_ids():
    service = DeepSeekLlmService(model="deepseek-flash", api_key="sk-test")

    assert service.model == "deepseek-flash"


def test_deepseek_can_omit_temperature():
    service = DeepSeekLlmService(
        model="deepseek-chat", api_key="sk-test", temperature=None
    )

    assert "temperature" not in service._build_payload(_request())


def test_deepseek_rejects_ollama_tags():
    with pytest.raises(ValueError, match="Ollama tag"):
        DeepSeekLlmService(model="qwen2.5:7b", api_key="sk-test")


def test_deepseek_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekLlmService(model="deepseek-chat")
