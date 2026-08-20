from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from llm_providers import (
    LLMClient,
    LLMPaymentRequiredError,
    ModelRequest,
    build_provider_spec,
)


def test_openrouter_request_keeps_exact_model_and_route_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def completion(**kwargs: Any) -> object:
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None,
        )

    monkeypatch.setattr("llm_providers.client.litellm.completion", completion)
    model = LLMClient(
        build_provider_spec(
            provider_id="openrouter",
            api_key="fixture-secret",
            model_id="openrouter/openai/gpt-5.6-luna",
        )
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "lookup",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    response = model.generate(
        ModelRequest(
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
        )
    )

    assert response.text == "ok"
    assert seen["model"] == "openrouter/openai/gpt-5.6-luna"
    assert seen["tools"] == tools
    assert seen["extra_body"] == {
        "provider": {
            "require_parameters": True,
            "data_collection": "deny",
        }
    }
    assert "models" not in seen


def test_openrouter_http_200_stream_error_uses_same_error_mapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = SimpleNamespace(
        error={"code": 402, "type": "payment_required", "message": "hidden"},
        choices=[],
    )
    monkeypatch.setattr(
        "llm_providers.client.litellm.completion",
        lambda **_: iter([chunk]),
    )
    model = LLMClient(
        build_provider_spec(provider_id="openrouter", api_key="fixture-secret"),
        network_retries=0,
        engine_overload_retries=0,
    )

    with pytest.raises(LLMPaymentRequiredError):
        list(model.stream(ModelRequest(messages=[{"role": "user", "content": "hi"}])))
