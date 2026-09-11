from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from llm_providers.chat_request import prepare_litellm_request
from llm_providers.deepseek_vision_compat import flatten_deepseek_file_blocks

from llm_providers import LLMBadRequestError, LLMClient, LLMTextDelta, LLMTurnDone, ProviderSpec


def _spec(
    model: str = "openai/deepseek-flash",
    *,
    defaults: dict[str, Any] | None = None,
) -> ProviderSpec:
    return ProviderSpec(
        model=model,
        api_key="secret",
        api_base="https://api.deepseek.com",
        defaults=defaults or {"extra_body": {"thinking": {"type": "disabled"}}},
        context_window=1_000_000,
    )


def _messages() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "file", "file": {"file_id": "file-one"}},
                {"type": "text", "text": "second"},
                {"type": "file", "file": {"file_id": "file-two"}},
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "inspect", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
    ]


def test_flatten_preserves_order_tool_history_and_input() -> None:
    messages = _messages()
    original = copy.deepcopy(messages)

    wire = flatten_deepseek_file_blocks(messages)

    assert messages == original
    assert wire[1]["content"] == [
        {"type": "text", "text": "first"},
        {"type": "file", "file_id": "file-one"},
        {"type": "text", "text": "second"},
        {"type": "file", "file_id": "file-two"},
    ]
    assert wire[2:] == original[2:]


@pytest.mark.parametrize(
    "block",
    (
        {"type": "file"},
        {"type": "file", "file": {}},
        {"type": "file", "file": {"file_id": ""}},
        {"type": "file", "file": {"file_id": "file-one", "name": "x"}},
        {"type": "file", "file": {"file_id": "file-one"}, "name": "x"},
    ),
)
def test_flatten_rejects_invalid_file_blocks(block: dict[str, Any]) -> None:
    with pytest.raises(LLMBadRequestError, match="attachment_wire_invalid"):
        flatten_deepseek_file_blocks([{"role": "user", "content": [block]}])


def test_flatten_rejects_file_block_outside_user_message() -> None:
    with pytest.raises(LLMBadRequestError, match="只能出现在 user message"):
        flatten_deepseek_file_blocks(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "file", "file": {"file_id": "file-one"}}],
                }
            ]
        )


def test_prepare_request_is_exact_model_gated_and_non_mutating() -> None:
    messages = _messages()
    options = {
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": "high",
        "temperature": 0.2,
    }
    original_options = copy.deepcopy(options)

    prepared_messages, prepared_options = prepare_litellm_request(_spec(), messages, options)

    assert prepared_messages is messages
    assert options == original_options
    assert prepared_options["extra_body"]["thinking"] == {"type": "enabled"}
    assert prepared_options["extra_body"]["messages"][1]["content"][1] == {
        "type": "file",
        "file_id": "file-one",
    }
    assert prepared_options["allowed_openai_params"] == ["reasoning_effort"]

    other_messages, other_options = prepare_litellm_request(
        _spec("deepseek/deepseek-flash"), messages, options
    )
    assert other_messages is messages
    assert other_options is options


def test_client_sync_keeps_litellm_safe_messages_and_adds_flat_wire_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None,
        )

    monkeypatch.setattr("llm_providers.client.litellm.completion", fake_completion)

    response = LLMClient(_spec()).complete(_messages())

    assert response == "ok"
    assert seen["model"] == "openai/deepseek-flash"
    assert seen["messages"][1]["content"][1] == {
        "type": "file",
        "file": {"file_id": "file-one"},
    }
    assert seen["extra_body"]["messages"][1]["content"][1] == {
        "type": "file",
        "file_id": "file-one",
    }
    assert seen["extra_body"]["thinking"] == {"type": "disabled"}
    assert "allowed_openai_params" not in seen


def test_client_stream_golden_http_body_uses_flat_deepseek_file_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        chunks = (
            'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,'
            '"model":"deepseek-flash","choices":[{"index":0,'
            '"delta":{"role":"assistant","content":"ok"},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,'
            '"model":"deepseek-flash","choices":[{"index":0,'
            '"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=chunks.encode(),
        )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "inspect",
                "description": "Inspect the image.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

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
    events = list(
        LLMClient(
            _spec(
                defaults={
                    "extra_body": {"thinking": {"type": "enabled"}},
                    "reasoning_effort": "high",
                }
            )
        ).stream(_messages(), tools=tools)
    )

    assert [event.text for event in events if isinstance(event, LLMTextDelta)] == ["ok"]
    done = next(event for event in events if isinstance(event, LLMTurnDone))
    assert done.diagnostics is not None
    assert done.diagnostics.request_witness is not None
    assert done.diagnostics.request_relation == "baseline"
    assert done.diagnostics.request_bytes == len(
        json.dumps(captured["body"], ensure_ascii=False, separators=(",", ":")).encode()
    )
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer secret"
    body = captured["body"]
    assert body["model"] == "deepseek-flash"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"
    assert body["tools"] == tools
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "first"},
        {"type": "file", "file_id": "file-one"},
        {"type": "text", "text": "second"},
        {"type": "file", "file_id": "file-two"},
    ]
    assert all(
        "file" not in block for block in body["messages"][1]["content"] if block["type"] == "file"
    )


def test_cancellable_stream_keeps_litellm_transport_and_wire_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_open(
        spec: ProviderSpec,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        cancel_check: Any,
        *,
        timeout_seconds: float,
    ) -> Any:
        seen.update(
            spec=spec,
            messages=messages,
            options=options,
            cancel_check=cancel_check,
            timeout_seconds=timeout_seconds,
        )
        return iter(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="ok", tool_calls=[]),
                            finish_reason="stop",
                        )
                    ],
                )
            ]
        )

    def cancel_check() -> None:
        return None

    monkeypatch.setattr("llm_providers.client.open_cancellable_litellm_stream", fake_open)
    monkeypatch.setattr(
        "llm_providers.client.litellm.completion",
        lambda **_kwargs: pytest.fail("cancellable route must use LiteLLM async transport"),
    )

    events = list(LLMClient(_spec()).stream(_messages(), _cancel_check=cancel_check))

    assert [event.text for event in events if isinstance(event, LLMTextDelta)] == ["ok"]
    assert seen["spec"].model == "openai/deepseek-flash"
    assert seen["messages"][1]["content"][1] == {
        "type": "file",
        "file": {"file_id": "file-one"},
    }
    assert seen["options"]["extra_body"]["messages"][1]["content"][1] == {
        "type": "file",
        "file_id": "file-one",
    }
    assert seen["cancel_check"] is cancel_check
