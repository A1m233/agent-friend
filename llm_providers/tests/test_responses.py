from __future__ import annotations

import asyncio
import copy
import json
import threading
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from llm_providers.catalog import PROVIDER_DEFINITIONS
from llm_providers.errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMIncompleteResponseError,
    LLMOutputBudgetExhaustedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMStreamInterruptedAfterOutputError,
)
from llm_providers.responses.request import prepare_request
from llm_providers.responses.stream import ResponsesState
from llm_providers.spec import ProviderSpec
from llm_providers.stream_events import LLMResponseDiagnostics, LLMRetryScheduled
from llm_providers.usage import extract_responses_usage

from llm_providers import (
    LanguageModelCapabilities,
    LLMClient,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelCallControl,
    ModelRequest,
    build_provider_spec,
    get_model_capabilities,
    public_model_catalog,
)


def message(identity: str, text: str, phase: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": identity,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    if phase is not None:
        result["phase"] = phase
    return result


def added(item: dict[str, Any], index: int) -> dict[str, Any]:
    initial = {**item, "status": "in_progress"}
    if item["type"] == "message":
        initial["content"] = []
    elif item["type"] == "function_call":
        initial["arguments"] = ""
    return {"type": "response.output_item.added", "output_index": index, "item": initial}


def delta(identity: str, index: int, text: str) -> dict[str, Any]:
    return {
        "type": "response.output_text.delta",
        "item_id": identity,
        "output_index": index,
        "content_index": 0,
        "delta": text,
    }


def terminal(
    items: list[dict[str, Any]], status: str = "completed", **extra: Any
) -> dict[str, Any]:
    return {
        "type": f"response.{status}",
        "response": {
            "id": "resp-1",
            "model": "fixture-model",
            "object": "response",
            "created_at": 1,
            "status": status,
            "output": items,
            **extra,
        },
    }


def text_events(phase: str | None = "final_answer") -> list[dict[str, Any]]:
    item = message("m1", "hello", phase)
    return [
        added(item, 0),
        delta("m1", 0, "hel"),
        delta("m1", 0, "lo"),
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        terminal(
            [item],
            usage={
                "input_tokens": 100,
                "output_tokens": 3,
                "total_tokens": 103,
                "input_tokens_details": {"cached_tokens": 70},
            },
        ),
    ]


@pytest.fixture
def http_mock(monkeypatch: pytest.MonkeyPatch) -> Any:
    requests: list[httpx.Request] = []

    def install(events: list[dict[str, Any]], status: int = 200) -> list[httpx.Request]:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if status != 200:
                return httpx.Response(
                    status, json={"error": {"type": "invalid_api_key", "message": "fixture"}}
                )
            if json.loads(request.content).get("stream") is False:
                return httpx.Response(
                    200, json=events[-1]["response"], headers={"x-request-id": "req-1"}
                )
            data = "".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
            )
            return httpx.Response(
                200,
                content=data.encode(),
                headers={"content-type": "text/event-stream", "x-request-id": "req-1"},
            )

        class MockClient(httpx.AsyncClient):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", MockClient)
        return requests

    return install


@pytest.mark.parametrize(
    "provider,model,path",
    [
        ("deepseek", "deepseek/deepseek-flash", "/responses"),
        ("deepseek", "deepseek/deepseek-v4-pro", "/responses"),
        ("kimi", "kimi/kimi-k3", "/v1/responses"),
    ],
)
def test_native_sdk_wire_and_normalized_events(
    http_mock: Any, monkeypatch: pytest.MonkeyPatch, provider: str, model: str, path: str
) -> None:
    requests = http_mock(text_events())
    monkeypatch.setattr(
        "llm_providers.client.litellm.completion", lambda **_: pytest.fail("Chat bridge called")
    )
    client = LLMClient(build_provider_spec(provider_id=provider, model_id=model, api_key="fixture"))
    events = list(client.stream(ModelRequest(messages=[{"role": "user", "content": "hi"}])))
    assert len(requests) == 1
    assert requests[0].url.path == path
    body = json.loads(requests[0].content)
    assert body["stream"] is True
    assert "messages" not in body and "stream_options" not in body
    assert body["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
    assert [(event.text, event.phase) for event in events if isinstance(event, LLMTextDelta)] == [
        ("hel", "final_answer"),
        ("lo", "final_answer"),
    ]
    done = events[-1]
    assert isinstance(done, LLMTurnDone)
    assert done.stop_reason == "end_turn"
    assert done.usage and done.usage.prompt_tokens == 100 and done.usage.cached_prompt_tokens == 70
    assert done.diagnostics and done.diagnostics.provider_request_id == "req-1"
    assert (
        done.diagnostics.request_witness
        and done.diagnostics.request_witness.transcript_field == "input"
    )


@pytest.mark.parametrize("phase", ["commentary", "final_answer", None, "future-phase"])
def test_phase_is_only_from_its_item(phase: str | None) -> None:
    state = ResponsesState(build_provider_spec(provider_id="deepseek", api_key="fixture"))
    events = [event for raw in text_events(phase) for event in state.feed(raw)]
    assert [event.phase for event in events if isinstance(event, LLMTextDelta)] == [
        phase if phase in {"commentary", "final_answer"} else None
    ] * 2


def test_interleaved_items_do_not_share_phase() -> None:
    state = ResponsesState(ProviderSpec("fixture", "fixture"))
    first, second = message("m1", "ab", "commentary"), message("m2", "xy", "final_answer")
    raw = [
        added(first, 0),
        added(second, 1),
        delta("m2", 1, "x"),
        delta("m1", 0, "a"),
        delta("m2", 1, "y"),
        delta("m1", 0, "b"),
        terminal([first, second]),
    ]
    events = [event for item in raw for event in state.feed(item)]
    assert [(event.text, event.phase) for event in events if isinstance(event, LLMTextDelta)] == [
        ("x", "final_answer"),
        ("a", "commentary"),
        ("y", "final_answer"),
        ("b", "commentary"),
    ]
    assert [
        (event.item_id, event.output_index) for event in events if isinstance(event, LLMTextDelta)
    ] == [
        ("m2", 1),
        ("m1", 0),
        ("m2", 1),
        ("m1", 0),
    ]
    done = state.finish(LLMResponseDiagnostics())
    assert done.assistant_turn is not None
    assert [
        (item.item_id, item.content, item.phase) for item in done.assistant_turn.text_items
    ] == [
        ("m1", "ab", "commentary"),
        ("m2", "xy", "final_answer"),
    ]
    assert [event.type for event in events].count("assistant_item_start") == 2
    assert [event.type for event in events].count("assistant_item_done") == 2


def test_parallel_functions_and_replay_request() -> None:
    spec = build_provider_spec(provider_id="deepseek", api_key="fixture")
    state = ResponsesState(spec)
    calls = [
        {
            "type": "function_call",
            "id": f"fc{i}",
            "call_id": f"call{i}",
            "name": "lookup",
            "arguments": f'{{"n":{i}}}',
            "status": "completed",
        }
        for i in range(2)
    ]
    raw = [added(item, index) for index, item in enumerate(calls)]
    for i in (1, 0):
        raw.extend(
            [
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": f"fc{i}",
                    "output_index": i,
                    "delta": '{"n":',
                },
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": f"fc{i}",
                    "output_index": i,
                    "arguments": f'{{"n":{i}}}',
                },
            ]
        )
    raw.append(terminal(calls))
    events = [event for item in raw for event in state.feed(item)]
    for index in range(2):
        assert (
            "".join(
                event.args_json_delta
                for event in events
                if isinstance(event, LLMToolCallDelta) and event.index == index
            )
            == f'{{"n":{index}}}'
        )
    done = state.finish(LLMResponseDiagnostics())
    assert done.stop_reason == "tool_use" and done.assistant_turn
    history = [
        done.assistant_turn.to_openai_message(),
        {"role": "tool", "tool_call_id": "call0", "content": "ok"},
    ]
    request = prepare_request(spec, history, None, {})  # type: ignore[arg-type]
    assert request["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call0",
        "output": "ok",
    }
    assert request["input"][0]["call_id"] == "call0"


def test_request_preserves_schema_images_settings_and_source() -> None:
    spec = build_provider_spec(
        provider_id="deepseek", api_key="fixture", model_id="deepseek/deepseek-flash"
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "read"},
                {"type": "file", "file": {"file_id": "file-image"}},
            ],
        }
    ]
    schema = {
        "type": "object",
        "properties": {"optional": {"type": "string"}},
        "additionalProperties": False,
    }
    tools = [
        {
            "type": "function",
            "function": {"name": "lookup", "description": "Find a record", "parameters": schema},
        }
    ]
    before = copy.deepcopy((messages, tools))
    request = prepare_request(spec, messages, tools, {"max_tokens": 80, "temperature": 0.3})
    assert request["max_output_tokens"] == 80
    assert request["input"][0]["content"][1] == {"type": "input_image", "file_id": "file-image"}
    assert request["tools"][0]["parameters"] == schema and "strict" not in request["tools"][0]
    assert (messages, tools) == before
    assert request["reasoning"] == {"effort": "none"}
    assert "extra_body" not in request


@pytest.mark.parametrize(
    "preset,effort", [("off", "none"), ("low", "low"), ("high", "high"), ("max", "max")]
)
def test_deepseek_thinking_preset_maps_to_native_reasoning(preset: str, effort: str) -> None:
    spec = build_provider_spec(provider_id="deepseek", api_key="fixture", reasoning_preset=preset)
    request = prepare_request(spec, [{"role": "user", "content": "hi"}], None, {})
    assert request["reasoning"] == {"effort": effort}
    assert "extra_body" not in request and "thinking" not in request


@pytest.mark.parametrize(
    "settings",
    [
        {"reasoning_effort": "high"},
        {"parallel_tool_calls": False},
        {"metadata": {"run": "fixture"}},
        {"prompt_cache_key": "fixture"},
    ],
)
def test_deepseek_rejects_conflicting_or_silently_ignored_options(settings: dict[str, Any]) -> None:
    with pytest.raises(LLMBadRequestError):
        prepare_request(
            build_provider_spec(provider_id="deepseek", api_key="fixture"), [], None, settings
        )


@pytest.mark.parametrize(
    "settings",
    [
        {"reasoning_effort": "none"},
        {"reasoning_effort": "medium"},
        {"parallel_tool_calls": False},
        {"temperature": 0.5},
        {"top_p": 0.8},
        {"metadata": {}},
        {"store": False},
        {"response_format": {"type": "json_object"}},
    ],
)
def test_kimi_rejects_settings_outside_documented_responses_contract(
    settings: dict[str, Any],
) -> None:
    spec = build_provider_spec(provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture")
    with pytest.raises(LLMBadRequestError):
        prepare_request(spec, [], None, settings)


def test_kimi_reasoning_cache_and_structured_output_mapping() -> None:
    spec = build_provider_spec(
        provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture", reasoning_preset="low"
    )
    schema = {"type": "object", "properties": {"value": {"type": "number"}}}
    result = prepare_request(
        spec,
        [],
        None,
        {
            "max_tokens": 100,
            "prompt_cache_key": "fixture-session",
            "tool_choice": "auto",
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema},
            },
        },
    )
    assert result["reasoning"] == {"effort": "low"}
    assert result["prompt_cache_key"] == "fixture-session"
    assert result["max_output_tokens"] == 100
    assert result["text"]["format"] == {"type": "json_schema", "name": "result", "schema": schema}


@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_k3_native_stream_keeps_reasoning_without_chat_compatibility_settings(
    http_mock: Any, monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    requests = http_mock(text_events())
    monkeypatch.setattr(
        "llm_providers.client.prepare_litellm_request",
        lambda *_: pytest.fail("Native Responses must not use Chat request preparation"),
    )
    spec = build_provider_spec(
        provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture", reasoning_preset=effort
    )

    events = list(
        LLMClient(spec).stream(ModelRequest(messages=[{"role": "user", "content": "hi"}]))
    )

    assert isinstance(events[-1], LLMTurnDone)
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/responses"
    body = json.loads(requests[0].content)
    assert body["reasoning"] == {"effort": effort}
    assert "allowed_openai_params" not in body and "drop_params" not in body
    assert "allowed_openai_params" not in spec.defaults


@pytest.mark.parametrize(
    "settings",
    [
        {"max_tokens": 1, "max_output_tokens": 2},
        {"extra_body": {"input": "override"}},
        {"unsupported": True},
        {"store": True},
    ],
)
def test_invalid_settings_fail_before_transport(settings: dict[str, Any]) -> None:
    with pytest.raises(LLMBadRequestError):
        prepare_request(
            build_provider_spec(provider_id="deepseek", api_key="fixture"), [], None, settings
        )


@pytest.mark.parametrize("cache", [None, 0, 70])
def test_responses_usage_is_inclusive(cache: int | None) -> None:
    usage = extract_responses_usage(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 7,
                "total_tokens": 107,
                "input_tokens_details": {"cached_tokens": cache, "cache_write_tokens": 10},
            }
        }
    )
    assert usage and usage.prompt_tokens == 100 and usage.total_tokens == 107
    assert usage.cached_prompt_tokens == cache and usage.cache_write_prompt_tokens == 10


@pytest.mark.parametrize(
    "status,details,expected",
    [
        ("incomplete", {"reason": "max_output_tokens"}, LLMOutputBudgetExhaustedError),
        ("incomplete", {"reason": "content_filter"}, LLMIncompleteResponseError),
        ("cancelled", {}, LLMIncompleteResponseError),
    ],
)
def test_non_success_never_emits_done(
    http_mock: Any, status: str, details: dict[str, str], expected: type[Exception]
) -> None:
    http_mock([terminal([], status, incomplete_details=details)])
    client = LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture"))
    with pytest.raises(expected):
        list(client.stream(ModelRequest(messages=[])))


def test_eof_does_not_complete(http_mock: Any) -> None:
    requests = http_mock(text_events()[:-1])
    client = LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture"))
    with pytest.raises(LLMIncompleteResponseError):
        list(client.stream(ModelRequest(messages=[])))
    assert len(requests) == 1


def test_http_auth_error_does_not_fallback(http_mock: Any) -> None:
    requests = http_mock([], 401)
    with pytest.raises(LLMAuthError):
        list(
            LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
                ModelRequest(messages=[])
            )
        )
    assert len(requests) == 1


def test_unknown_item_fails_but_completed_phase_can_change() -> None:
    state = ResponsesState(ProviderSpec("fixture", "fixture"))
    with pytest.raises(LLMProviderError, match="unknown"):
        state.feed(delta("unknown", 0, "text"))
    item = message("m1", "hi", "commentary")
    state.feed(added(item, 0))
    state.feed(delta("m1", 0, "hi"))
    state.feed(terminal([{**item, "phase": "final_answer"}]))
    done = state.finish(LLMResponseDiagnostics())
    assert done.assistant_turn is not None
    assert done.assistant_turn.text_items[0].phase == "final_answer"


@pytest.mark.parametrize("complete_phase", ["commentary", "final_answer", None, "illegal"])
@pytest.mark.parametrize("split", range(6))
def test_phase_updates_at_item_and_response_completion(
    complete_phase: str | None, split: int
) -> None:
    from llm_providers import LLMAssistantItemDone

    state = ResponsesState(ProviderSpec("fixture", "fixture"))
    start = message("m1", "hello", "final_answer")
    item_done = message("m1", "hello", "commentary")
    events = state.feed(added(start, 0))
    events += state.feed(delta("m1", 0, "hello"[:split]))
    events += state.feed(delta("m1", 0, "hello"[split:]))
    events += state.feed(
        {"type": "response.output_item.done", "output_index": 0, "item": item_done}
    )
    events += state.feed(terminal([message("m1", "hello", complete_phase)]))
    assert "".join(event.text for event in events if isinstance(event, LLMTextDelta)) == "hello"
    assert [event.phase for event in events if isinstance(event, LLMAssistantItemDone)] == [
        "commentary"
    ]
    done = state.finish(LLMResponseDiagnostics())
    assert done.assistant_turn is not None
    assert done.assistant_turn.text_items[0].phase == (
        complete_phase if complete_phase in {"commentary", "final_answer"} else None
    )


@pytest.mark.parametrize(
    "change",
    [
        {"id": "different"},
        {"role": "user"},
        {"content": [{"type": "output_text", "text": "different"}]},
    ],
)
def test_phase_update_does_not_allow_other_completed_snapshot_changes(
    change: dict[str, Any],
) -> None:
    state = ResponsesState(ProviderSpec("fixture", "fixture"))
    item = message("m1", "hello", "final_answer")
    state.feed(added(item, 0))
    state.feed({"type": "response.output_item.done", "output_index": 0, "item": item})
    with pytest.raises(LLMProviderError):
        state.feed(terminal([{**item, "phase": "commentary", **change}]))


def test_catalog_capabilities_and_hidden_legacy_provider() -> None:
    for provider in PROVIDER_DEFINITIONS.values():
        for model in provider.models:
            spec = build_provider_spec(
                provider_id=provider.provider_id, model_id=model.id, api_key="fixture"
            )
            expected = provider.provider_id == "deepseek" or model.id == "kimi/kimi-k3"
            assert (spec.responses is not None) == expected
            capability = get_model_capabilities(LLMClient(spec)).assistant_message_phase
            assert capability == ("native" if provider.provider_id == "deepseek" else "unavailable")
            usage_scope = get_model_capabilities(LLMClient(spec)).input_usage_scope
            assert usage_scope == ("opaque" if spec.responses is not None else "request")
    assert {item["id"] for item in public_model_catalog()["providers"]} == {"deepseek", "kimi"}
    assert build_provider_spec(provider_id="openrouter", api_key="fixture").responses is None


def test_capability_query_preserves_old_model_contract() -> None:
    legacy = SimpleNamespace(context_window=100, generate=lambda _: None, stream=lambda _: iter(()))
    assert get_model_capabilities(legacy) == LanguageModelCapabilities()
    assert get_model_capabilities(legacy).input_usage_scope == "opaque"
    legacy.capabilities = {"assistant_message_phase": "native"}
    assert get_model_capabilities(legacy) == LanguageModelCapabilities()
    legacy.capabilities = LanguageModelCapabilities(assistant_message_phase="native")
    assert get_model_capabilities(legacy).assistant_message_phase == "native"


@pytest.mark.parametrize("nonstream", [False, True])
def test_header_wait_is_cancellable(monkeypatch: pytest.MonkeyPatch, nonstream: bool) -> None:
    started, closed = threading.Event(), threading.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
        raise AssertionError("unreachable")

    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)

    class Cancelled(Exception):
        pass

    def cancel() -> None:
        if started.is_set():
            raise Cancelled()

    request = ModelRequest(messages=[], control=ModelCallControl(cancel_check=cancel))
    with pytest.raises(Cancelled):
        model = LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture"))
        if nonstream:
            model.generate(request)
        else:
            list(model.stream(request))
    assert closed.wait(1)


@pytest.mark.parametrize(
    "provider,model",
    [
        ("deepseek", "deepseek/deepseek-flash"),
        ("kimi", "kimi/kimi-k3"),
    ],
)
@pytest.mark.parametrize("entry", ["product", "memory_eval"])
def test_generate_responses_final_json(
    http_mock: Any, provider: str, model: str, entry: str
) -> None:
    requests = http_mock(
        [
            terminal(
                [
                    message("progress", "I will extract memory.", "commentary"),
                    message("final", '{"entries": []}', "final_answer"),
                ]
            )
        ]
    )
    spec = build_provider_spec(provider_id=provider, model_id=model, api_key="fixture")
    if entry == "memory_eval":
        from memory_eval.__main__ import _provider_spec

        from runtime_config import EnvSource

        spec = _provider_spec(
            model, EnvSource({"DEEPSEEK_API_KEY": "fixture", "KIMI_API_KEY": "fixture"})
        )
    client = LLMClient(spec)
    response = client.generate(ModelRequest(messages=[{"role": "user", "content": "extract"}]))
    assert json.loads(response.text) == {"entries": []}
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/responses")
    assert json.loads(requests[0].content)["stream"] is False
    assert response.diagnostics and response.diagnostics.transport == "responses"
    assert response.diagnostics.provider_request_id == "req-1"


@pytest.mark.parametrize(
    "items",
    [
        [message("a", "thinking", "commentary")],
        [message("a", "one", "final_answer"), message("b", "two", "final_answer")],
        [message("a", "one"), message("b", "two")],
    ],
)
def test_generate_rejects_ambiguous_results(http_mock: Any, items: list[dict[str, Any]]) -> None:
    requests = http_mock([terminal(items)])
    client = LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture"))
    with pytest.raises(LLMIncompleteResponseError):
        client.complete([{"role": "user", "content": "hi"}])
    assert len(requests) == 1


def test_generate_accepts_unphased_result(http_mock: Any) -> None:
    http_mock([terminal([message("a", "plain JSON")])])
    client = LLMClient(
        build_provider_spec(provider_id="kimi", model_id="kimi/kimi-k3", api_key="fixture")
    )
    assert client.complete([{"role": "user", "content": "hi"}]) == "plain JSON"


def test_legacy_generate_and_stream_stay_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def complete(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if kwargs.get("stream"):
            return iter(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="legacy", tool_calls=None),
                                finish_reason="stop",
                            )
                        ],
                        usage=None,
                    )
                ]
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="direct"))], usage=None
        )

    monkeypatch.setattr("llm_providers.client.litellm.completion", complete)
    spec = build_provider_spec(provider_id="deepseek", api_key="fixture")
    client = LLMClient(replace(spec, responses=None))
    assert (
        client.generate(ModelRequest(messages=[{"role": "user", "content": "hi"}])).text == "direct"
    )
    assert client.complete([{"role": "user", "content": "hi"}]) == "direct"
    legacy = LLMClient(replace(spec, responses=None))
    text = [
        event
        for event in legacy.stream(ModelRequest(messages=[]))
        if isinstance(event, LLMTextDelta)
    ]
    assert text == [LLMTextDelta("legacy", phase=None)]
    assert len(calls) == 3


@pytest.mark.parametrize("after_output", [False, True])
def test_network_retry_boundary(monkeypatch: pytest.MonkeyPatch, after_output: bool) -> None:
    calls: list[int] = []

    class Stream:
        request_id = "req"
        request_witness = None

        def __iter__(self) -> Any:
            calls.append(1)
            if len(calls) == 1:
                if after_output:
                    yield from text_events()[:2]
                raise ConnectionError("fixture interruption")
            yield from text_events()

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "llm_providers.responses.stream.open_responses_stream", lambda *args: Stream()
    )
    client = LLMClient(
        build_provider_spec(provider_id="deepseek", api_key="fixture"),
        network_retry_backoff_seconds=0,
    )
    if after_output:
        with pytest.raises(LLMStreamInterruptedAfterOutputError):
            list(client.stream(ModelRequest(messages=[])))
        assert len(calls) == 1
    else:
        events = list(client.stream(ModelRequest(messages=[])))
        assert (
            len(calls) == 2 and sum(isinstance(event, LLMRetryScheduled) for event in events) == 1
        )
        assert isinstance(events[-1], LLMTurnDone)


def test_failed_event_maps_rate_limit_and_preserves_usage(http_mock: Any) -> None:
    http_mock(
        [
            terminal(
                [],
                "failed",
                error={"code": "rate_limit_exceeded", "message": "fixture"},
                usage={"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
            )
        ]
    )
    with pytest.raises(LLMRateLimitError) as error:
        list(
            LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
                ModelRequest(messages=[])
            )
        )
    assert error.value.usage.prompt_tokens == 10  # type: ignore[attr-defined]


def test_incomplete_item_keeps_terminal_usage(http_mock: Any) -> None:
    item = message("m1", "half", "commentary")
    http_mock(
        [
            added(item, 0),
            delta("m1", 0, "half"),
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {**item, "status": "incomplete"},
            },
            terminal(
                [item],
                "incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                usage={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            ),
        ]
    )
    with pytest.raises(LLMIncompleteResponseError) as error:
        list(
            LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
                ModelRequest(messages=[])
            )
        )
    assert (
        error.value.stop_reason == "max_output_tokens"
        and error.value.usage
        and error.value.usage.total_tokens == 14
    )


def test_generator_close_releases_source(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = []

    class Stream:
        request_id = None
        request_witness = None

        def __iter__(self) -> Any:
            yield from text_events()

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        "llm_providers.responses.stream.open_responses_stream", lambda *args: Stream()
    )
    events = LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
        ModelRequest(messages=[])
    )
    next(events)
    events.close()  # type: ignore[attr-defined]
    assert closed == [True]


def test_reasoning_round_trip_remains_opaque() -> None:
    spec = build_provider_spec(provider_id="kimi", api_key="fixture", model_id="kimi/kimi-k3")
    reasoning = {
        "id": "rs1",
        "type": "reasoning",
        "status": "completed",
        "summary": [],
        "encrypted_content": "opaque-fixture",
    }
    item = message("m1", "answer")
    state = ResponsesState(spec)
    state.feed(terminal([reasoning, item]))
    done = state.finish(LLMResponseDiagnostics())
    assert done.assistant_turn and done.reasoning_state
    request = prepare_request(spec, [done.assistant_turn.to_openai_message()], None, {})
    assert request["input"][0] == reasoning
    assert "opaque-fixture" not in repr(done)


def test_completed_response_cannot_accept_an_incomplete_item() -> None:
    state = ResponsesState(ProviderSpec("fixture", "fixture"))
    item = message("m1", "partial")
    state.feed(added(item, 0))
    item["status"] = "incomplete"
    state.feed({"type": "response.output_item.done", "output_index": 0, "item": item})
    with pytest.raises(LLMProviderError, match="did not complete"):
        state.feed(terminal([item]))


def test_conflicting_reasoning_sources_are_rejected() -> None:
    with pytest.raises(LLMBadRequestError, match="conflicting reasoning"):
        prepare_request(
            build_provider_spec(provider_id="deepseek", api_key="fixture"),
            [],
            None,
            {"reasoning": {"effort": "low"}, "extra_body": {"reasoning": {"effort": "high"}}},
        )


@pytest.mark.parametrize(
    "entry",
    [
        {"role": "tool", "tool_call_id": "call1", "content": "result", "phase": "commentary"},
        {"role": "user", "content": "hello", "tool_call_id": "call1"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call1",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": "{}",
                        "unknown": True,
                    },
                }
            ],
        },
    ],
)
def test_unrepresentable_message_fields_fail_closed(entry: dict[str, Any]) -> None:
    with pytest.raises(LLMBadRequestError):
        prepare_request(
            build_provider_spec(provider_id="deepseek", api_key="fixture"), [entry], None, {}
        )


def test_event_wait_cancellation_closes_http_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    started, closed = threading.Event(), threading.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            started.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self) -> None:
            closed.set()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Body(), headers={"content-type": "text/event-stream"})

    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)

    class Cancelled(Exception):
        pass

    def cancel() -> None:
        if started.is_set():
            raise Cancelled()

    with pytest.raises(Cancelled):
        list(
            LLMClient(build_provider_spec(provider_id="deepseek", api_key="fixture")).stream(
                ModelRequest(messages=[], control=ModelCallControl(cancel_check=cancel))
            )
        )
    assert closed.wait(1)
