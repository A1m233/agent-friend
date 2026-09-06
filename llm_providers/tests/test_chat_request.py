from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import openai
import pytest
from llm_providers.chat_request import prepare_litellm_request
from llm_providers.request_witness import ProviderRequestCapture

from llm_providers import (
    LLMBadRequestError,
    LLMClient,
    ModelRequest,
    ProviderSpec,
    build_provider_spec,
)


@pytest.fixture
def chat_http(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Exercise real LiteLLM validation and SDK serialization with no provider call."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "fixture-chat",
                "object": "chat.completion",
                "created": 0,
                "model": "kimi-k3",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "fixture summary"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    def instrumented_client(
        spec: ProviderSpec, capture: ProviderRequestCapture
    ) -> tuple[openai.OpenAI, openai.OpenAI]:
        session = httpx.Client(
            transport=httpx.MockTransport(handler),
            event_hooks={"request": [capture.observe]},
        )
        client = openai.OpenAI(
            api_key=spec.api_key,
            base_url=spec.api_base,
            max_retries=0,
            http_client=session,
        )
        return client, client

    monkeypatch.setattr("llm_providers.litellm_transport._sync_client", instrumented_client)
    return requests


@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_k3_generate_preserves_effort_through_real_litellm(
    chat_http: list[httpx.Request], effort: str
) -> None:
    spec = build_provider_spec(
        provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture", reasoning_preset=effort
    )
    request = ModelRequest(
        messages=[
            {"role": "system", "content": "Summarize briefly."},
            {"role": "user", "content": "Synthetic context."},
        ],
        settings={"max_tokens": 4096},
    )
    original = copy.deepcopy((spec.defaults, request.messages, request.settings))

    response = LLMClient(spec).generate(request)

    assert response.text == "fixture summary"
    assert len(chat_http) == 1
    assert str(chat_http[0].url) == "https://api.moonshot.cn/v1/chat/completions"
    body = json.loads(chat_http[0].content)
    assert body["model"] == "kimi-k3"
    assert body["reasoning_effort"] == effort
    assert body["max_tokens"] == 4096
    assert "allowed_openai_params" not in body and "drop_params" not in body
    assert "stream" not in body
    assert response.diagnostics is not None
    assert response.diagnostics.request_witness is not None
    assert response.diagnostics.transport == "litellm"
    assert (spec.defaults, request.messages, request.settings) == original


def test_k3_complete_preserves_per_request_effort_override(chat_http: list[httpx.Request]) -> None:
    spec = build_provider_spec(
        provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture", reasoning_preset="max"
    )

    text = LLMClient(spec).complete(
        [{"role": "user", "content": "Summarize briefly."}], reasoning_effort="low", max_tokens=4096
    )

    assert text == "fixture summary"
    assert len(chat_http) == 1
    assert json.loads(chat_http[0].content)["reasoning_effort"] == "low"
    assert spec.defaults["reasoning_effort"] == "max"
    assert "allowed_openai_params" not in spec.defaults


@pytest.mark.parametrize("existing", [[], ["temperature"], ("reasoning_effort",)])
def test_k3_allowlist_preserves_existing_values_without_mutating_inputs(existing: Any) -> None:
    spec = build_provider_spec(provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture")
    options = {"reasoning_effort": "low", "allowed_openai_params": existing}
    original = copy.deepcopy(options)

    _, prepared = prepare_litellm_request(spec, [], options)

    assert prepared["reasoning_effort"] == "low"
    assert prepared["allowed_openai_params"] == list(dict.fromkeys([*existing, "reasoning_effort"]))
    assert options == original


@pytest.mark.parametrize(
    "model", ["openai/kimi-k2.7-code", "openai/kimi-k3-other", "openai/gpt-4o"]
)
def test_k3_compatibility_does_not_allow_parameters_for_other_models(model: str) -> None:
    spec = ProviderSpec(model=model, api_key="fixture")
    options = {"reasoning_effort": "low"}

    _, prepared = prepare_litellm_request(spec, [], options)

    assert prepared is options
    assert "allowed_openai_params" not in prepared


@pytest.mark.parametrize("allowed", ["reasoning_effort", [1]])
def test_k3_rejects_malformed_allowlist(allowed: Any) -> None:
    spec = build_provider_spec(provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture")

    with pytest.raises(LLMBadRequestError, match="allowed_openai_params"):
        prepare_litellm_request(
            spec, [], {"reasoning_effort": "low", "allowed_openai_params": allowed}
        )
