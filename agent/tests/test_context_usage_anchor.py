"""Measured input must dominate old-history estimation error, without hiding edits."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent.attachments import AttachmentRef
from agent.context import SummarizingContextManager
from agent.context.usage_anchor import (
    RequestMeasurement,
    UsageAnchor,
    digest,
    estimate_input,
    estimate_rendered_tokens,
)
from agent.messages import Message
from agent.sessions.rollout import SessionRolloutRecorder, restore_rollout
from agent.tools import ToolRegistry
from agent.tools.builtin.calculate.tool import CalculateTool

from agent import Conversation
from llm_providers import (
    LanguageModelCapabilities,
    LLMContextWindowError,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
    ModelRequest,
    ModelResponse,
)

from .test_context_summarizing import _make_conversation


def measured(messages: list[dict[str, Any]], tokens: int = 111_680) -> UsageAnchor:
    result = UsageAnchor.from_usage(
        RequestMeasurement.capture(messages, digest("route")),
        LLMUsage(prompt_tokens=tokens, input_token_accounting="inclusive_total"),
    )
    assert result is not None
    return result


def test_small_delta_does_not_repeat_old_history_overestimate() -> None:
    prefix: list[dict[str, Any]] = [{"role": "user", "content": "a" * 273_000}]
    anchor = measured(prefix)
    delta: list[dict[str, Any]] = [
        {"role": "assistant", "content": "查询", "tool_calls": [{"id": "t", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "t", "content": "x" * 8000},
    ]
    result = estimate_input(
        prefix + delta,
        scope=digest("route"),
        anchor=anchor,
        fallback_tokens=211_310,
    )
    assert result.tokens == 111_680 + estimate_rendered_tokens(delta)
    assert result.tokens < 209_716
    assert result.mode == "usage_anchor"
    grown = [*delta, {"role": "user", "content": "字" * 150_000}]
    assert (
        estimate_input(
            prefix + grown,
            scope=digest("route"),
            anchor=anchor,
            fallback_tokens=999_999,
        ).tokens
        > 209_716
    )


@pytest.mark.parametrize("change", ["edit", "truncate", "reorder", "system", "scope"])
def test_rewrites_cannot_spend_old_usage(change: str) -> None:
    prefix = [{"role": "system", "content": "rules"}, {"role": "user", "content": "old"}]
    anchor = measured(prefix)
    messages = [dict(item) for item in prefix]
    scope = digest("route")
    if change == "edit":
        messages[-1]["content"] = "new"
    elif change == "truncate":
        messages.pop()
    elif change == "reorder":
        messages.reverse()
    elif change == "system":
        messages[0]["content"] = "new rules"
    else:
        scope = digest("different provider/tools/settings/session")
    result = estimate_input(messages, scope=scope, anchor=anchor, fallback_tokens=250_000)
    assert result.mode == "full_estimate"
    assert result.tokens == 250_000


@pytest.mark.parametrize("tokens", [0, -1, True, 1.5, float("nan"), "10"])
def test_invalid_usage_is_never_an_anchor(tokens: Any) -> None:
    request = RequestMeasurement.capture([{"role": "user", "content": "hi"}], digest("r"))
    assert (
        UsageAnchor.from_usage(
            request,
            LLMUsage(prompt_tokens=tokens, input_token_accounting="inclusive_total"),
        )
        is None
    )


def test_cached_input_is_inclusive_and_output_is_not_reused_as_input() -> None:
    messages = [{"role": "user", "content": "hi"}]
    request = RequestMeasurement.capture(messages, digest("r"))
    usage = LLMUsage(
        prompt_tokens=1000,
        completion_tokens=9000,
        total_tokens=10000,
        cached_prompt_tokens=900,
        input_token_accounting="inclusive_total",
    )
    anchor = UsageAnchor.from_usage(request, usage)
    assert anchor is not None and anchor.input_tokens == 1000
    assert (
        UsageAnchor.from_usage(request, replace(usage, input_token_accounting="unavailable"))
        is None
    )
    assert UsageAnchor.from_usage(request, replace(usage, cached_prompt_tokens=1001)) is None
    assert UsageAnchor.from_usage(request, None) is None


def test_restore_rechecks_scope_and_exact_prefix_and_rejects_future_metadata() -> None:
    messages = [{"role": "user", "content": "hi"}]
    anchor = measured(messages, 100)
    restored = UsageAnchor.restore(anchor.payload())
    assert restored == anchor
    assert UsageAnchor.restore({**anchor.payload(), "version": 3}) is None
    assert UsageAnchor.restore({**anchor.payload(), "version": 1}) == anchor
    assert UsageAnchor.restore({**anchor.payload(), "input_tokens": True}) is None
    assert UsageAnchor.restore({**anchor.payload(), "prefix": "invalid"}) is None
    assert UsageAnchor.restore(None) is None
    assert (
        estimate_input(
            [*messages, {"role": "assistant", "content": "ok"}],
            scope=digest("route"),
            anchor=restored,
            fallback_tokens=500,
        ).tokens
        < 500
    )


def test_rendered_reasoning_is_counted_but_image_base64_is_not_text() -> None:
    plain = [{"role": "assistant", "content": "ok"}]
    reasoning = [{**plain[0], "reasoning_content": "推理" * 1000}]
    assert estimate_rendered_tokens(reasoning) > estimate_rendered_tokens(plain) + 1000

    def image(size: int) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + "A" * size},
                    }
                ],
            }
        ]

    assert estimate_rendered_tokens(image(100)) == estimate_rendered_tokens(image(100_000))


@dataclass
class MeasuredLLM:
    capabilities = LanguageModelCapabilities(input_usage_scope="request")
    context_window: int = 100_000
    prompt_tokens: int | None = 5000
    received: list[list[dict[str, Any]]] = field(default_factory=list)

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="<summary>earlier conversation</summary>")

    def stream(self, request: ModelRequest) -> Iterator[Any]:
        self.received.append(request.messages)
        yield LLMTextDelta(text="ok")
        usage = (
            LLMUsage(prompt_tokens=self.prompt_tokens, input_token_accounting="inclusive_total")
            if self.prompt_tokens is not None
            else None
        )
        yield LLMTurnDone(stop_reason="end_turn", usage=usage)


def test_conversation_reuses_measured_prefix_and_verified_rollout_after_restart(
    tmp_path: Path,
) -> None:
    llm = MeasuredLLM(context_window=100_000, prompt_tokens=5000)
    conv, session, store = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    assert conv.send("first " + "a" * 20_000) == "ok"
    assert conv.send("second") == "ok"
    notes = conv.last_context_notes
    assert notes["estimation_mode"] == "usage_anchor"
    assert notes["anchor_input_tokens"] == 5000
    assert 5000 <= notes["token_estimate"] < 6000

    reloaded = store.load(session.session_id)
    restored = restore_rollout(reloaded)
    restarted = Conversation(
        session=reloaded,
        store=store,
        llm_client=llm,
        context_manager=SummarizingContextManager(),
        prompt_builder=conv._prompt_builder,
        session_model_history=restored.history,
        session_model_context=restored.model_context,
        cache_epoch=restored.cache_epoch,
        catalog=conv._catalog,
    )
    assert restarted.send("third") == "ok"
    assert restarted.last_context_notes["estimation_mode"] == "usage_anchor"
    assert restarted.last_context_notes["token_estimate"] < 6000


def test_request_scope_covers_tools_settings_and_reasoning(tmp_path: Path) -> None:
    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=MeasuredLLM(),
        context_manager=SummarizingContextManager(),
    )
    original = conv._usage_scope(None)
    assert original == conv._usage_scope([], {})
    assert original != conv._usage_scope([{"type": "function", "function": {"name": "new"}}])
    assert original != conv._usage_scope(None, {"temperature": 0.1})


def test_final_oversized_request_is_rebuilt_before_any_provider_call(tmp_path: Path) -> None:
    llm = MeasuredLLM(context_window=100_000)
    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    conv._last_context_notes = {"context_strategy": "summarizing", "trigger_threshold": 100}
    repaired = [{"role": "user", "content": "small"}]
    list(
        conv._iter_model_stream(
            [{"role": "user", "content": "x" * 1000}],
            tools=None,
            recovery_builder=lambda: repaired,
        )
    )
    assert llm.received == [repaired]


def test_missing_new_usage_cannot_persist_a_previous_request_anchor(tmp_path: Path) -> None:
    llm = MeasuredLLM(context_window=100_000, prompt_tokens=5000)
    conv, session, _ = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    conv.send("first")
    assert conv._usage_anchor is not None
    llm.prompt_tokens = None
    conv.send("second")
    assert conv._usage_anchor is None
    assistant = next(e for e in reversed(session.events) if e.type == "assistant_message")
    assert assistant.meta["context_usage"]["input_anchor"] is None


def test_valid_anchor_does_not_compute_full_fallback() -> None:
    messages = [{"role": "user", "content": "hi"}]

    def forbidden() -> int:
        pytest.fail("re-estimated old history")

    result = estimate_input(
        messages,
        scope=digest("route"),
        anchor=measured(messages, 100),
        fallback_tokens=forbidden,
    )
    assert result.tokens == 100


def test_attachment_candidates_do_not_invoke_uploading_renderer(tmp_path: Path) -> None:
    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=MeasuredLLM(),
        context_manager=SummarizingContextManager(),
    )

    def forbidden(message: Any, client: Any) -> dict[str, Any]:
        pytest.fail("uploaded a budget candidate")

    conv._message_renderer = forbidden
    attachment = AttachmentRef("asset", "image.png", "image", "image/png", 100, "a" * 64)
    estimate = conv._estimate_context_input(
        [
            Message(role="user", content="look", attachments=[attachment]),
        ],
        None,
    )
    assert estimate.reason == "attachments_pending_render"
    assert estimate.tokens >= 1536


def test_opaque_provider_state_and_route_change_do_not_reuse_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = MeasuredLLM()
    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    messages = [{"role": "user", "content": "hi"}]
    conv._usage_anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(messages, conv._usage_scope(None)),
        LLMUsage(prompt_tokens=5000, input_token_accounting="inclusive_total"),
    )
    monkeypatch.setattr(
        llm, "spec", SimpleNamespace(api_base="https://other.invalid"), raising=False
    )
    assert conv._estimate_rendered_input(messages, None).reason == "request_scope_changed"
    monkeypatch.setattr(llm, "spec", SimpleNamespace(responses="server-owned"))
    monkeypatch.setattr(llm, "capabilities", LanguageModelCapabilities(input_usage_scope="opaque"))
    assert conv._estimate_rendered_input(messages, None).reason == "opaque_provider_continuation"


def test_tool_loop_uses_latest_input_usage_and_counts_results(tmp_path: Path) -> None:
    class ToolLLM(MeasuredLLM):
        def stream(self, request: ModelRequest) -> Iterator[Any]:
            if not self.received:
                self.received.append(request.messages)
                yield LLMToolCallDelta(
                    index=0,
                    tool_call_id="calc",
                    tool_name="calculate",
                    args_json_delta='{"operation":"evaluate","expression":"17*23"}',
                )
                yield LLMTurnDone(
                    stop_reason="tool_use",
                    usage=LLMUsage(
                        prompt_tokens=5000,
                        input_token_accounting="inclusive_total",
                    ),
                )
            else:
                yield from super().stream(request)

    llm = ToolLLM()
    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    conv._tool_registry = ToolRegistry([CalculateTool()])
    assert conv.send("calculate") == "ok"
    assert len(llm.received) == 2
    assert conv.last_context_notes["estimation_mode"] == "usage_anchor"
    assert conv.last_context_notes["incremental_tokens"] > 0
    assert any(item.get("role") == "tool" and "391" in str(item) for item in llm.received[1])


def test_final_budget_recovery_is_bounded(tmp_path: Path) -> None:
    llm = MeasuredLLM()
    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=llm,
        context_manager=SummarizingContextManager(),
    )
    conv._last_context_notes = {"context_strategy": "summarizing", "trigger_threshold": 100}
    oversized = [{"role": "user", "content": "x" * 1000}]
    with pytest.raises(LLMContextWindowError, match="final_request_budget_exceeded"):
        list(conv._iter_model_stream(oversized, tools=None, recovery_builder=lambda: oversized))
    assert not llm.received
    assert conv._pending_request_measurement is None


def test_native_file_uses_declared_cost_instead_of_file_id_or_base64(tmp_path: Path) -> None:
    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=MeasuredLLM(),
        context_manager=SummarizingContextManager(),
    )
    conv._last_built_model_messages = [
        Message(
            role="user",
            content="",
            attachments=[
                AttachmentRef("asset", "file.pdf", "document", "application/pdf", 40000, "f" * 64)
            ],
        )
    ]

    def request(file: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"role": "user", "content": [{"type": "file", "file": file}]}]

    by_id = conv._estimate_rendered_input(request({"file_id": "abc"}), None)
    by_data = conv._estimate_rendered_input(request({"file_data": "A" * 1_000_000}), None)
    assert by_id.tokens == by_data.tokens == 10000
    assert by_id.reason == "opaque_attachment"


def test_normalized_exclusive_cache_buckets_are_already_included() -> None:
    request = RequestMeasurement.capture([{"role": "user", "content": "hi"}], digest("r"))
    anchor = UsageAnchor.from_usage(
        request,
        LLMUsage(
            prompt_tokens=1000,
            cached_prompt_tokens=800,
            cache_write_prompt_tokens=100,
            input_token_accounting="exclusive_components",
        ),
    )
    assert anchor is not None and anchor.input_tokens == 1000
