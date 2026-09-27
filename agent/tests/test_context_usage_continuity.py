"""Local changes must not discard the measured cost of unchanged history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent.attachments import AttachmentRef
from agent.context import SummarizingContextManager
from agent.context.budget import estimate_tool_schema_tokens
from agent.context.usage_anchor import (
    RequestMeasurement,
    UsageAnchor,
    digest,
    estimate_full_input,
    estimate_input,
)
from agent.messages import Message
from agent.sessions.rollout import SessionRolloutRecorder, restore_rollout
from agent.tools import ToolRegistry
from agent.tools.builtin.calculate.tool import CalculateTool

from agent import Conversation
from llm_providers import LLMUsage

from .test_context_summarizing import _make_conversation
from .test_context_usage_anchor import MeasuredLLM


def anchor_for(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    tokens: int = 118652,
    reasoning: Any = None,
) -> UsageAnchor:
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(
            messages, digest(tools), compatibility_scope=digest("compatible"), tools=tools
        ),
        LLMUsage(
            prompt_tokens=tokens, completion_tokens=100, input_token_accounting="inclusive_total"
        ),
        reasoning_details=reasoning,
    )
    assert anchor is not None
    return anchor


def estimate(
    messages: list[dict[str, Any]],
    anchor: UsageAnchor,
    tools: list[dict[str, Any]] | None = None,
    fallback: int = 254111,
) -> Any:
    return estimate_input(
        messages,
        scope=digest(tools),
        compatibility_scope=digest("compatible"),
        anchor=anchor,
        tools=tools,
        fallback_tokens=fallback,
        unknown_token_reserve=272000,
    )


def test_tool_and_system_changes_retain_measured_history_without_running_fallback() -> None:
    history = [{"role": "system", "content": "old"}, {"role": "user", "content": "a" * 330000}]
    tools = [{"type": "function", "function": {"name": "tool", "description": "old"}}]
    anchor = anchor_for(history, tools)
    changed = [{"role": "system", "content": "new"}, history[1]]
    new_tools = [{"type": "function", "function": {"name": "tool", "description": "new"}}]

    def forbidden() -> int:
        pytest.fail("unchanged history was fully re-estimated")

    result = estimate_input(
        changed,
        scope=digest(new_tools),
        compatibility_scope=digest("compatible"),
        tools=new_tools,
        anchor=anchor,
        fallback_tokens=forbidden,
    )
    assert result.tokens == 118652 + 2 + estimate_tool_schema_tokens(new_tools)
    assert result.tokens < 217600
    assert result.mode == "usage_anchor_partial"
    assert result.retained_components == 2
    assert result.reused_components == 3
    assert result.tokens == result.anchor_tokens + result.delta_tokens
    assert (
        estimate([*changed, {"role": "user", "content": "x" * 150000}], anchor, new_tools).tokens
        > 217600
    )


def test_duplicate_occurrences_role_changes_and_reordering() -> None:
    item = {"role": "user", "content": "abcd"}
    other = {"role": "assistant", "content": "efgh"}
    anchor = anchor_for([item, other], tokens=100)
    assert estimate([other, item], anchor).tokens == 100
    assert estimate([item, other, item], anchor).tokens == 103
    result = estimate([item, {"role": "tool", "content": "efgh", "tool_call_id": "t"}], anchor)
    assert result.tokens == 103
    assert result.retained_components == 2


def test_deleted_cost_is_not_subtracted_and_repeated_checks_do_not_accumulate() -> None:
    old = [{"role": "user", "content": "a" * 10000}, {"role": "assistant", "content": "ok"}]
    anchor = anchor_for(old, tokens=100)
    edited = [{"role": "user", "content": "new"}, old[1]]
    assert estimate(edited, anchor).tokens == 102
    assert estimate(edited, anchor).tokens == 102
    assert estimate([old[1]], anchor).tokens == 100
    # A compacted candidate has an independently small full cost, never a
    # subtraction of the old 7500-token heuristic from the 100-token measurement.
    assert estimate([old[1]], anchor, fallback=2).tokens == 2
    refreshed = anchor_for(edited, tokens=60)
    assert estimate(edited, refreshed).tokens == 60


def test_restore_v3_roundtrip_validates_components_and_identity() -> None:
    messages = [{"role": "user", "content": "secret history"}]
    anchor = anchor_for(messages)
    payload = json.loads(json.dumps(anchor.payload()))
    assert "secret history" not in json.dumps(payload)
    assert UsageAnchor.restore(payload) == anchor
    for invalid in (None, [], ["bad"], [True]):
        assert UsageAnchor.restore({**payload, "component_digests": invalid}) is None
    assert UsageAnchor.restore({**payload, "compatibility_scope": None}) is None
    assert UsageAnchor.restore({**payload, "version": 99}) is None
    result = estimate_input(
        messages,
        scope=anchor.scope,
        compatibility_scope=digest("other model/session"),
        anchor=anchor,
        fallback_tokens=300000,
    )
    assert result.mode == "full_estimate" and result.reason == "request_scope_changed"


def test_measured_reasoning_survives_text_edit_and_unknown_reasoning_is_reserved() -> None:
    opaque = [{"type": "reasoning", "encrypted_content": "A" * 900000}]
    old = [{"role": "assistant", "content": "old", "reasoning_details": opaque}]
    anchor = anchor_for(old, tokens=100)
    changed = [{**old[0], "content": "new"}]
    assert estimate(changed, anchor).tokens == 102
    unseen = [{"role": "assistant", "reasoning_details": [{"encrypted_content": "B"}]}]
    result = estimate(old + unseen, anchor)
    assert result.unknown_tokens == 272000
    assert result.tokens >= 272000
    # Only matching output reasoning can spend the last response's output bound.
    bound_anchor = anchor_for(old, tokens=100, reasoning=unseen[0]["reasoning_details"])
    assert estimate(old + unseen, bound_anchor).tokens == 200


def test_unchanged_native_file_is_reused_and_new_file_cost_is_not_base64_length() -> None:
    old = [{"role": "user", "content": [{"type": "input_file", "file_id": "old"}]}]
    anchor = anchor_for(old, tokens=1000)
    assert estimate(old, anchor).tokens == 1000
    for data in ("B", "B" * 1000000):
        new = [{"role": "user", "content": [{"type": "file", "file": {"file_data": data}}]}]
        result = estimate(old + new, anchor)
        assert result.tokens == 1000 + 272000
        assert result.unknown_components == 1
        assert result.notes()["input_cost_known"] is False


def test_conversation_tool_change_and_restart_keep_usage_below_compaction_threshold(
    tmp_path: Path,
) -> None:
    llm = MeasuredLLM(context_window=100000, prompt_tokens=5000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    conv._rollout_recorder = SessionRolloutRecorder(store=store, session=session)
    conv.send("a" * 40000)
    llm.context_window = 30000
    conv._tool_registry = ToolRegistry([CalculateTool()])
    assert conv.send("continue") == "ok"
    assert conv.last_context_notes["token_estimate"] < 24000
    assert conv.last_context_notes["anchor_input_tokens"] == 5000
    assert not any(event.type == "compaction" for event in session.events)
    restored_session = store.load(session.session_id)
    rollout = restore_rollout(restored_session)
    restarted = Conversation(
        session=restored_session,
        store=store,
        llm_client=llm,
        context_manager=SummarizingContextManager(),
        prompt_builder=conv._prompt_builder,
        session_model_history=rollout.history,
        session_model_context=rollout.model_context,
        cache_epoch=rollout.cache_epoch,
        catalog=conv._catalog,
    )
    # Tool removal across processes is a local edit, not a route change.
    assert restarted.send("again") == "ok"
    assert restarted.last_context_notes["estimation_mode"] == "usage_anchor_partial"
    assert restarted.last_context_notes["token_estimate"] < 24000
    assert not any(event.type == "compaction" for event in restored_session.events)


def test_attachment_candidate_preserves_plain_history_without_upload(tmp_path: Path) -> None:
    conv, _, _ = _make_conversation(
        tmp_path, fake_llm=MeasuredLLM(), context_manager=SummarizingContextManager()
    )
    history = [{"role": "user", "content": "a" * 40000}]
    conv._usage_anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(
            history,
            conv._usage_scope(None),
            compatibility_scope=conv._usage_scope(None, compatibility_only=True),
        ),
        LLMUsage(prompt_tokens=5000, input_token_accounting="inclusive_total"),
    )

    def renderer(message: Message, client: Any) -> dict[str, Any]:
        assert not message.attachments and not message.images, "uploaded candidate"
        return message.to_openai()

    conv._message_renderer = renderer
    result = conv._estimate_context_input(
        [
            Message(role="user", content=history[0]["content"]),
            Message(
                role="user",
                content="look",
                attachments=[
                    AttachmentRef("asset", "image.png", "image", "image/png", 100, "a" * 64)
                ],
            ),
        ],
        None,
    )
    assert 6536 <= result.tokens < 7000
    assert result.anchor_tokens == 5000
    assert result.reason == "attachments_pending_render"


def test_same_length_summary_replacement_can_release_retained_slack(tmp_path: Path) -> None:
    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=MeasuredLLM(context_window=10000),
        context_manager=SummarizingContextManager(),
    )
    old = [{"role": "system", "content": "rules"}, {"role": "user", "content": "x" * 16000}]
    conv._usage_anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(
            old,
            conv._usage_scope(None),
            compatibility_scope=conv._usage_scope(None, compatibility_only=True),
        ),
        LLMUsage(prompt_tokens=9000, input_token_accounting="inclusive_total"),
    )
    summary = [old[0], {"role": "system", "content": "summary"}]
    result = conv._estimate_rendered_input(summary, None)
    assert result.tokens < 100
    assert result.reason == "rewritten_candidate"
    # Sending this rewritten candidate must not resurrect the old input floor.
    assert list(conv._iter_model_stream(summary, tools=None))


def test_full_candidate_estimate_never_prices_reasoning_by_ciphertext_length() -> None:
    for size in (1, 900000):
        message = {"role": "assistant", "reasoning_details": [{"encrypted_content": "A" * size}]}
        assert estimate_full_input([message], None, unknown_token_reserve=272000) == 272000
        anchor = anchor_for([message], tokens=1000)
        assert (
            estimate_full_input([message], None, unknown_token_reserve=272000, anchor=anchor)
            == 1000
        )
        # An extra occurrence cannot reuse the one measured copy for free.
        assert (
            estimate_full_input(
                [message, message], None, unknown_token_reserve=272000, anchor=anchor
            )
            == 273000
        )
