from __future__ import annotations

import json
from typing import Any

import httpx
import openai
import pytest
from llm_providers.request_witness import (
    ProviderRequestCapture,
    ProviderRequestWitness,
    witness_from_body,
)

from llm_providers import (
    LLMClient,
    LLMTurnDone,
    ModelCallControl,
    ModelRequest,
    ProviderSpec,
)


def _body(
    messages: list[dict[str, object]],
    *,
    temperature: float = 0.2,
) -> bytes:
    return json.dumps(
        {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _witness(
    body: bytes,
    *,
    transform: str = "litellm:1:deepseek-chat",
) -> ProviderRequestWitness:
    return witness_from_body(
        body,
        provider_id="deepseek",
        transport="litellm",
        transform_identity=transform,
    )


def test_final_wire_witness_classifies_retry_extension_and_rebase() -> None:
    first_messages: list[dict[str, object]] = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "work"},
    ]
    first = _witness(_body(first_messages))
    retry = _witness(_body(first_messages))
    extended = _witness(
        _body(
            [
                *first_messages,
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"q":"x"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "result"},
            ]
        )
    )

    assert first.relation_to(None) == "baseline"
    assert retry.relation_to(first) == "same_request_retry"
    assert extended.relation_to(first) == "strict_extension"
    assert extended.common_prefix_items(first) == len(first_messages)
    assert _witness(_body([*first_messages], temperature=0.3)).relation_to(first) == "rebase"
    assert (
        _witness(_body(first_messages), transform="litellm:2:deepseek-chat").relation_to(first)
        == "rebase"
    )


def test_final_wire_witness_reports_item_role_domain_and_lost_prefix() -> None:
    previous = _witness(
        _body(
            [
                {"role": "system", "content": "stable"},
                {
                    "role": "system",
                    "content": ("<agent_friend_runtime_context>old</agent_friend_runtime_context>"),
                },
                {"role": "user", "content": "history"},
            ]
        )
    )
    current = _witness(
        _body(
            [
                {"role": "system", "content": "stable"},
                {
                    "role": "system",
                    "content": ("<agent_friend_runtime_context>new</agent_friend_runtime_context>"),
                },
                {"role": "user", "content": "history"},
            ]
        )
    )

    assert previous.transcript_item_roles == ("system", "system", "user")
    assert previous.transcript_item_domains == ("system", "runtime", "messages")
    assert current.relation_to(previous) == "rebase"
    assert current.common_prefix_items(previous) == 1
    assert current.first_divergence_index(previous) == 1
    assert current.first_divergence_role(previous) == "system"
    assert current.first_divergence_domain(previous) == "runtime"
    assert current.lost_prefix_items(previous) == 2


def test_witness_diagnostics_retain_no_request_content() -> None:
    secret = "PRIVATE_USER_AND_TOOL_CONTENT"
    witness = _witness(
        _body(
            [
                {"role": "user", "content": secret},
                {"role": "tool", "tool_call_id": "call-1", "content": secret},
            ]
        )
    )

    assert witness.request_bytes > 0
    assert len(witness.request_fingerprint) == 24
    assert secret not in repr(witness)
    assert secret not in json.dumps(witness.__dict__, default=str)
    assert all(len(item) == 64 for item in witness.transcript_item_digests)


def test_non_json_wire_body_degrades_without_guessing_transcript() -> None:
    witness = _witness(b"opaque-provider-payload")

    assert witness.transcript_field is None
    assert witness.transcript_item_digests == ()
    assert witness.envelope_digest is None
    assert witness.relation_to(None) == "baseline"


def test_memory_domain_sidecar_requires_exact_final_item_alignment() -> None:
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "question"},
        {"role": "system", "content": "memory payload"},
    ]
    capture = ProviderRequestCapture(
        provider_id="test",
        transport="litellm",
        transform_identity="test:v1",
        transcript_items=messages,
        transcript_item_domains=("system", "messages", "memory"),
    )
    request = httpx.Request(
        "POST",
        "https://example.test/v1/chat/completions",
        json={"messages": messages},
    )
    capture.observe(request)

    assert capture.witness is not None
    assert capture.witness.contributor_mapping_precision == "exact_item"
    assert capture.witness.transcript_item_domains[-1] == "memory"

    mismatched = ProviderRequestCapture(
        provider_id="test",
        transport="litellm",
        transform_identity="test:v1",
        transcript_items=messages,
        transcript_item_domains=("system", "messages", "memory"),
    )
    mismatched.observe(
        httpx.Request(
            "POST",
            "https://example.test/v1/chat/completions",
            json={"messages": [*messages, {"role": "user", "content": "extra"}]},
        )
    )
    assert mismatched.witness is not None
    assert mismatched.witness.contributor_mapping_precision == "unavailable"
    assert "memory" not in mismatched.witness.transcript_item_domains


def test_moved_previous_memory_is_the_first_divergence_domain() -> None:
    previous_messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "first"},
        {"role": "system", "content": "memory"},
    ]
    current_messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
        {"role": "system", "content": "memory"},
    ]

    def captured(
        messages: list[dict[str, Any]],
        domains: tuple[str, ...],
    ) -> ProviderRequestWitness:
        capture = ProviderRequestCapture(
            provider_id="test",
            transport="litellm",
            transform_identity="test:v1",
            transcript_items=messages,
            transcript_item_domains=domains,
        )
        capture.observe(
            httpx.Request(
                "POST",
                "https://example.test/v1/chat/completions",
                json={"messages": messages},
            )
        )
        assert capture.witness is not None
        return capture.witness

    previous = captured(previous_messages, ("system", "messages", "memory"))
    current = captured(
        current_messages,
        ("system", "messages", "messages", "messages", "memory"),
    )

    assert current.first_divergence_index(previous) == 2
    assert current.first_divergence_previous_domain(previous) == "memory"
    assert current.first_divergence_current_domain(previous) == "messages"
    assert current.first_divergence_domain(previous) == "memory"


def test_non_stream_completion_captures_actual_http_body_and_compares_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": f"response-{len(bodies)}",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 1,
                    "total_tokens": 11,
                },
            },
        )

    def instrumented_client(spec: ProviderSpec, capture: Any) -> tuple[Any, Any]:
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
    client = LLMClient(
        ProviderSpec(
            model="openai/test-model",
            api_key="secret",
            api_base="https://example.test/v1",
            context_window=8_192,
        ),
        network_retries=0,
    )
    first_messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "first"},
    ]
    first = client.generate(
        ModelRequest(
            messages=first_messages,
            control=ModelCallControl(
                transcript_item_domains=("system", "messages"),
            ),
        )
    )
    assert first.diagnostics is not None
    first_witness = first.diagnostics.request_witness
    assert first_witness is not None
    assert first.diagnostics.request_relation == "baseline"
    assert first_witness.contributor_mapping_precision == "exact_item"

    second_messages = [
        *first_messages,
        {"role": "assistant", "content": "answer"},
    ]
    second = client.generate(
        ModelRequest(
            messages=second_messages,
            control=ModelCallControl(
                previous_request_witness=first_witness,
                transcript_item_domains=("system", "messages", "messages"),
            ),
        )
    )

    assert bodies[1]["messages"][:2] == bodies[0]["messages"]
    assert second.diagnostics is not None
    assert second.diagnostics.request_relation == "strict_extension"
    assert second.diagnostics.common_prefix_items == 2


def test_kimi_direct_witness_comes_from_actual_http_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        chunks = (
            'data: {"id":"kimi-1","object":"chat.completion.chunk","created":1,'
            '"model":"kimi-k2.6","choices":[{"index":0,'
            '"delta":{"role":"assistant","content":"ok"},"finish_reason":null}]}\n\n'
            'data: {"id":"kimi-1","object":"chat.completion.chunk","created":1,'
            '"model":"kimi-k2.6","choices":[{"index":0,'
            '"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=chunks.encode(),
        )

    def instrumented_client(spec: ProviderSpec, capture: Any) -> openai.OpenAI:
        session = httpx.Client(
            transport=httpx.MockTransport(handler),
            event_hooks={"request": [capture.observe]},
        )
        return openai.OpenAI(
            api_key=spec.api_key,
            base_url=spec.api_base,
            max_retries=0,
            http_client=session,
        )

    monkeypatch.setattr(
        "llm_providers.kimi_transport._sync_client",
        instrumented_client,
    )
    client = LLMClient(
        ProviderSpec(
            model="openai/kimi-k2.6",
            api_key="secret",
            api_base="https://api.moonshot.cn/v1",
            context_window=262_144,
        )
    )

    events = list(client.stream([{"role": "user", "content": "wire"}]))
    done = next(event for event in events if isinstance(event, LLMTurnDone))

    assert captured["body"] == {
        "messages": [{"role": "user", "content": "wire"}],
        "model": "kimi-k2.6",
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    assert done.diagnostics is not None
    assert done.diagnostics.request_witness is not None
    assert done.diagnostics.request_bytes is not None
    assert done.diagnostics.request_bytes > 0
    assert done.diagnostics.request_relation == "baseline"
    assert done.diagnostics.request_fingerprint == (
        done.diagnostics.request_witness.request_fingerprint
    )


def test_same_request_retry_preserves_wire_body_and_reports_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(
                503,
                headers={"content-type": "application/json"},
                json={"error": {"message": "busy", "type": "service_unavailable"}},
            )
        chunks = (
            'data: {"id":"kimi-2","object":"chat.completion.chunk","created":1,'
            '"model":"kimi-k2.6","choices":[{"index":0,'
            '"delta":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=chunks.encode(),
        )

    def instrumented_client(spec: ProviderSpec, capture: Any) -> openai.OpenAI:
        session = httpx.Client(
            transport=httpx.MockTransport(handler),
            event_hooks={"request": [capture.observe]},
        )
        return openai.OpenAI(
            api_key=spec.api_key,
            base_url=spec.api_base,
            max_retries=0,
            http_client=session,
        )

    monkeypatch.setattr("llm_providers.kimi_transport._sync_client", instrumented_client)
    diagnostics: list[Any] = []
    client = LLMClient(
        ProviderSpec(
            model="openai/kimi-k2.6",
            api_key="secret",
            api_base="https://api.moonshot.cn/v1",
            context_window=262_144,
        ),
        network_retries=1,
        network_retry_backoff_seconds=0,
    )

    events = list(
        client.stream(
            [{"role": "user", "content": "retry"}],
            _diagnostic_callback=diagnostics.append,
        )
    )
    done = next(event for event in events if isinstance(event, LLMTurnDone))

    assert len(bodies) == 2
    assert bodies[1] == bodies[0]
    assert next(
        event for event in diagnostics if event.phase == "request_open_failed"
    ).request_relation == ("baseline")
    assert done.diagnostics is not None
    assert done.diagnostics.request_relation == "same_request_retry"


def test_consecutive_actual_http_requests_report_strict_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        chunks = (
            'data: {"id":"kimi-3","object":"chat.completion.chunk","created":1,'
            '"model":"kimi-k2.6","choices":[{"index":0,'
            '"delta":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=chunks.encode(),
        )

    def instrumented_client(spec: ProviderSpec, capture: Any) -> openai.OpenAI:
        session = httpx.Client(
            transport=httpx.MockTransport(handler),
            event_hooks={"request": [capture.observe]},
        )
        return openai.OpenAI(
            api_key=spec.api_key,
            base_url=spec.api_base,
            max_retries=0,
            http_client=session,
        )

    monkeypatch.setattr("llm_providers.kimi_transport._sync_client", instrumented_client)
    client = LLMClient(
        ProviderSpec(
            model="openai/kimi-k2.6",
            api_key="secret",
            api_base="https://api.moonshot.cn/v1",
            context_window=262_144,
        )
    )
    baseline: list[dict[str, Any]] = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "work"},
    ]
    first_events = list(client.stream(ModelRequest(messages=baseline)))
    first_done = next(event for event in first_events if isinstance(event, LLMTurnDone))
    assert first_done.diagnostics is not None
    first_witness = first_done.diagnostics.request_witness
    assert first_witness is not None
    extended: list[dict[str, Any]] = [
        *baseline,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q":"x"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        {
            "role": "user",
            "content": '<agent_friend_runtime_context sequence="1" supersedes="0" />',
        },
    ]
    second_events = list(
        client.stream(
            ModelRequest(
                messages=extended,
                control=ModelCallControl(previous_request_witness=first_witness),
            )
        )
    )
    second_done = next(event for event in second_events if isinstance(event, LLMTurnDone))

    assert bodies[1]["messages"][: len(bodies[0]["messages"])] == bodies[0]["messages"]
    assert second_done.diagnostics is not None
    assert second_done.diagnostics.request_relation == "strict_extension"
    assert second_done.diagnostics.common_prefix_items == len(baseline)
