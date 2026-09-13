"""Responses budgets cross the actual adapter, tool loop and durable rollout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent.context import SummarizingContextManager
from agent.context.usage_anchor import RequestMeasurement, UsageAnchor, digest, estimate_input
from agent.sessions.rollout import SessionRolloutRecorder, restore_rollout
from agent.tools import ToolRegistry
from agent.tools.builtin.calculate.tool import CalculateTool
from llm_providers.spec import ProviderSpec, ResponsesRoute

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    Session,
)
from llm_providers import LLMClient, LLMUsage


def reasoning(identity: str, size: int = 60_000) -> dict[str, Any]:
    return {"id": identity, "type": "reasoning", "summary": [], "encrypted_content": "x" * size}


@pytest.mark.parametrize("changed_tools", [False, True])
def test_responses_tool_loop_and_restart_use_actual_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_tools: bool
) -> None:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert body["store"] is False
        assert "previous_response_id" not in body
        requests.append(body)
        index = len(requests)
        items = [reasoning(f"r{index}")]
        if index == 1:
            items.append(
                {
                    "id": "fc1",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": "calc",
                    "name": "calculate",
                    "arguments": '{"operation":"evaluate","expression":"17*23"}',
                }
            )
        else:
            items.append(
                {
                    "id": f"m{index}",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                }
            )
        event = {
            "type": "response.completed",
            "response": {
                "id": f"resp{index}",
                "model": "fixture",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "output": items,
                "usage": {
                    "input_tokens": 101_840 + index * 1000,
                    "output_tokens": 1400,
                    "total_tokens": 103_240 + index * 1000,
                    "input_tokens_details": {"cached_tokens": 100_000},
                    "output_tokens_details": {"reasoning_tokens": 1300},
                },
            },
        }
        return httpx.Response(
            200,
            content=f"event: response.completed\ndata: {json.dumps(event)}\n\n".encode(),
            headers={"content-type": "text/event-stream"},
        )

    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    model_id = "custom/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222"
    client = LLMClient(
        ProviderSpec(
            "openai/fixture",
            "fixture",
            context_window=272_000,
            provider_id="custom/11111111-1111-4111-8111-111111111111",
            product_model_id=model_id,
            reasoning_replay_scope="conversation",
            responses=ResponsesRoute("fixture", "https://fixture.invalid/v1"),
        )
    )
    monkeypatch.setattr(client, "generate", lambda _: pytest.fail("premature compaction"))
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    session = Session.new(
        title="Responses usage",
        persona="default",
        model=model_id,
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    store.create(session)
    conv = Conversation(
        session=session,
        store=store,
        llm_client=client,
        context_manager=SummarizingContextManager(),
        catalog=catalog,
        prompt_builder=MarkdownPromptBuilder(
            persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog
        ),
    )
    conv._tool_registry = ToolRegistry([CalculateTool()])
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    assert conv.send("a" * 250_000) == "ok"
    assert len(requests) == 2
    assert any(
        i.get("type") == "function_call_output" and "391" in str(i) for i in requests[1]["input"]
    )
    assert reasoning("r1") in requests[1]["input"]
    assert conv.last_context_notes["estimation_mode"] == "usage_anchor"
    assert 102_840 < conv.last_context_notes["token_estimate"] < 110_000
    assert conv.send("continue") == "ok"
    assert conv.last_context_notes["estimation_mode"] == "usage_anchor"
    assert conv.last_context_notes["token_estimate"] < 110_000
    assert reasoning("r2") in requests[2]["input"]
    assert not any(e.type == "compaction" for e in session.events)

    reloaded = store.load(session.session_id)
    restored = restore_rollout(reloaded)
    restarted = Conversation(
        session=reloaded,
        store=store,
        llm_client=client,
        context_manager=SummarizingContextManager(),
        prompt_builder=conv._prompt_builder,
        session_model_history=restored.history,
        session_model_context=restored.model_context,
        cache_epoch=restored.cache_epoch,
        catalog=conv._catalog,
        tool_registry=ToolRegistry([] if changed_tools else [CalculateTool()]),
    )
    assert restarted.send("after restart") == "ok"
    assert restarted.last_context_notes["estimation_mode"] == (
        "usage_anchor_partial" if changed_tools else "usage_anchor"
    )
    assert restarted.last_context_notes["token_estimate"] < 110_000
    assert reasoning("r3") in requests[3]["input"]


@pytest.mark.parametrize("size", [1, 100_000])
def test_opaque_reasoning_uses_matching_output_bound_only_when_replayed(size: int) -> None:
    prefix = [{"role": "user", "content": "old"}]
    details = [reasoning("r1", size)]
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(prefix, digest("route")),
        LLMUsage(
            prompt_tokens=1000, completion_tokens=9000, input_token_accounting="inclusive_total"
        ),
        reasoning_details=details,
    )
    assert anchor is not None
    anchor = UsageAnchor.restore(anchor.payload())
    assert anchor is not None
    assert "encrypted_content" not in json.dumps(anchor.payload())

    def estimate(delta: list[dict[str, Any]]) -> Any:
        return estimate_input(
            prefix + delta, scope=digest("route"), anchor=anchor, fallback_tokens=99_999
        )

    assert estimate([]).tokens == 1000
    replay = {"role": "assistant", "content": "ok", "reasoning_details": details}
    assert 10_000 <= estimate([replay]).tokens < 10_010
    # A small opaque ID is no cheaper than long ciphertext. Different payloads
    # cannot borrow this response's bound, even if their ID is the same.
    unknown = {**replay, "reasoning_details": [reasoning("r1", size + 1)]}
    assert estimate([unknown]).reason == "unmeasured_reasoning_delta"
    assert estimate([unknown]).tokens == 99_999


@pytest.mark.parametrize("output", [None, 0, -1, True, "10", 1.5])
def test_missing_or_invalid_output_usage_cannot_price_opaque_delta(output: Any) -> None:
    prefix = [{"role": "user", "content": "old"}]
    details = [reasoning("r", 1)]
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(prefix, digest("route")),
        LLMUsage(
            prompt_tokens=1000, completion_tokens=output, input_token_accounting="inclusive_total"
        ),
        reasoning_details=details,
    )
    result = estimate_input(
        [*prefix, {"role": "assistant", "content": "", "reasoning_details": details}],
        scope=digest("route"),
        anchor=anchor,
        fallback_tokens=50_000,
    )
    assert result.reason == "unmeasured_reasoning_delta"
    assert result.tokens == 50_000


def test_changed_reasoning_in_measured_prefix_invalidates_anchor() -> None:
    prefix = [{"role": "assistant", "content": "ok", "reasoning_details": [reasoning("r", 1)]}]
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(prefix, digest("route")),
        LLMUsage(prompt_tokens=1000, input_token_accounting="inclusive_total"),
    )
    result = estimate_input(
        [{**prefix[0], "reasoning_details": [reasoning("r", 2)]}],
        scope=digest("route"),
        anchor=anchor,
        fallback_tokens=50_000,
    )
    assert result.reason == "request_prefix_changed"


def test_reasoning_already_measured_is_not_charged_twice_but_large_delta_is() -> None:
    details = [reasoning("old", 1)]
    prefix = [{"role": "assistant", "content": "ok", "reasoning_details": details}]
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(prefix, digest("route")),
        LLMUsage(
            prompt_tokens=100_000,
            completion_tokens=150_000,
            input_token_accounting="inclusive_total",
        ),
        reasoning_details=[reasoning("new", 1)],
    )
    measured = estimate_input(
        prefix,
        scope=digest("route"),
        anchor=anchor,
        fallback_tokens=999_999,
    )
    assert measured.tokens == 100_000
    assert measured.mode == "usage_anchor"
    grown = estimate_input(
        [*prefix, {"role": "assistant", "content": "", "reasoning_details": [reasoning("new", 1)]}],
        scope=digest("route"),
        anchor=anchor,
        fallback_tokens=999_999,
    )
    assert grown.tokens == 250_000
    assert grown.tokens > 217_600


@pytest.mark.parametrize(
    "changes",
    [
        {"output_reasoning_tokens": True},
        {"output_reasoning_tokens": -1},
        {"output_reasoning_digest": None},
        {"output_reasoning_digest": "not-a-digest"},
    ],
)
def test_invalid_durable_reasoning_bound_is_rejected(changes: dict[str, Any]) -> None:
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture([{"role": "user", "content": "hi"}], digest("route")),
        LLMUsage(
            prompt_tokens=1000, completion_tokens=2000, input_token_accounting="inclusive_total"
        ),
        reasoning_details=[reasoning("r", 1)],
    )
    assert anchor is not None
    assert UsageAnchor.restore({**anchor.payload(), **changes}) is None
