from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from llm_providers import (
    LLMClient,
    LLMTurnDone,
    ModelRequest,
    ModelRuntimeConfig,
    ProviderReasoningState,
    build_provider_spec,
    render_provider_reasoning_state,
)


def _chunk(delta: object, *, finish_reason: str | None = None) -> object:
    return SimpleNamespace(
        id="response-1",
        model="fixture-model",
        usage=None,
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
    )


def test_stream_collects_reasoning_content_without_emitting_it_as_text(monkeypatch: Any) -> None:
    chunks = [
        _chunk(SimpleNamespace(reasoning_content="private ", content=None, tool_calls=[])),
        _chunk(SimpleNamespace(reasoning_content="state", content=None, tool_calls=[])),
        _chunk(SimpleNamespace(reasoning_content=None, content="answer", tool_calls=[])),
        _chunk(
            SimpleNamespace(reasoning_content=None, content=None, tool_calls=[]),
            finish_reason="stop",
        ),
    ]
    monkeypatch.setattr("llm_providers.client.litellm.completion", lambda **_: iter(chunks))
    client = LLMClient(
        build_provider_spec(
            provider_id="deepseek",
            api_key="fixture",
            model_id="deepseek/deepseek-v4-pro",
            reasoning_preset="high",
        )
    )

    events = list(client.stream(ModelRequest(messages=[{"role": "user", "content": "hi"}])))
    done = next(item for item in events if isinstance(item, LLMTurnDone))

    assert done.reasoning_state is not None
    assert done.reasoning_state.format == "reasoning_content"
    assert done.reasoning_state.payload == "private state"
    assert all(getattr(item, "text", "") != "private state" for item in events)
    assert "private state" not in repr(done.reasoning_state)


def test_stream_preserves_reasoning_detail_chunks_in_order(monkeypatch: Any) -> None:
    first = {"type": "reasoning.text", "text": "part", "index": 0}
    second = {"type": "reasoning.encrypted", "data": "opaque", "index": 1}
    chunks = [
        _chunk(SimpleNamespace(reasoning_details=[first], content=None, tool_calls=[])),
        _chunk(SimpleNamespace(reasoning_details=[second], content="ok", tool_calls=[])),
        _chunk(
            SimpleNamespace(reasoning_details=None, content=None, tool_calls=[]),
            finish_reason="stop",
        ),
    ]
    monkeypatch.setattr("llm_providers.client.litellm.completion", lambda **_: iter(chunks))
    client = LLMClient(
        build_provider_spec(
            provider_id="openrouter",
            api_key="fixture",
            model_id="openrouter/openai/gpt-5.6-luna",
            reasoning_preset="high",
        )
    )

    done = next(
        item
        for item in client.stream(ModelRequest(messages=[{"role": "user", "content": "hi"}]))
        if isinstance(item, LLMTurnDone)
    )

    assert done.reasoning_state is not None
    assert done.reasoning_state.format == "reasoning_details"
    assert done.reasoning_state.payload == (first, second)


def test_reasoning_state_round_trip_and_replay_scope() -> None:
    state = ProviderReasoningState(
        provider_id="kimi",
        model_id="kimi/kimi-k2.6",
        format="reasoning_content",
        replay_scope="tool_turn",
        payload="private",
    )
    restored = ProviderReasoningState.from_event_payload(state.to_event_payload())
    message = {"role": "assistant", "content": "visible"}
    config = ModelRuntimeConfig("kimi/kimi-k2.6", "on")

    assert "reasoning_content" not in render_provider_reasoning_state(
        message,
        restored,
        config=config,
        state_run_id="run-1",
        active_run_id="run-2",
    )
    assert (
        render_provider_reasoning_state(
            message,
            restored,
            config=config,
            state_run_id="run-1",
            active_run_id="run-1",
        )["reasoning_content"]
        == "private"
    )
    assert "reasoning_content" not in render_provider_reasoning_state(
        message,
        restored,
        config=ModelRuntimeConfig("kimi/kimi-k2.6", "off"),
        state_run_id="run-1",
        active_run_id="run-1",
    )
