"""Candidate preparation preserves admission evidence and has a search-local lifetime."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from agent.attachments import AttachmentRef
from agent.context import SummarizingContextManager
from agent.context import usage_anchor as ua
from agent.context.usage_anchor import InputBudgetCache, RequestMeasurement, UsageAnchor, digest
from agent.messages import Message
from llm_providers.catalog import KIMI_MULTIMODAL_CAPABILITIES
from llm_providers.spec import ResponsesRoute

from llm_providers import LLMClient, LLMUsage, MultimodalInputAdapter, ProviderSpec

from .test_context_summarizing import _make_conversation


@pytest.mark.parametrize("version", [0, 2, 4])
@pytest.mark.parametrize("compatible", [False, True])
def test_cached_candidates_preserve_all_budget_evidence(version: int, compatible: bool) -> None:
    opaque = {"role": "assistant", "reasoning_details": [{"encrypted_content": "opaque"}]}
    output = {"role": "assistant", "reasoning_details": [{"encrypted_content": "new"}]}
    document = {"role": "user", "content": [{"type": "input_file", "file_id": "doc"}]}
    image = {
        "role": "tool",
        "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}],
    }
    history: list[dict[str, Any]] = [
        {"role": "user", "content": "old " * 1000},
        opaque,
        document,
        image,
        {
            "role": "assistant",
            "tool_calls": [{"id": "call", "function": {"name": "read", "arguments": "{}"}}],
        },
    ]
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(
            history, digest("request"), compatibility_scope=digest("route"), tools=tools
        ),
        LLMUsage(
            prompt_tokens=31_500, completion_tokens=100, input_token_accounting="inclusive_total"
        ),
        reasoning_details=output["reasoning_details"],
    )
    assert anchor is not None
    if version == 0:
        anchor = None
    elif version == 2:
        anchor = replace(anchor, version=2, compatibility_scope=None, component_digests=())
    cache = InputBudgetCache()
    candidates = [history[cut:] for cut in range(len(history) + 1)]
    candidates.extend(
        [[opaque, opaque], [output], [output, output], [document, document], [image, image]]
    )
    candidates.extend(
        [[{"role": "assistant", "reasoning_details": [{"encrypted_content": "unknown"}]}]]
    )
    for candidate in candidates:
        for candidate_tools in (tools, [*tools, tools[0]], []):

            def assess(
                prepared: InputBudgetCache | None,
                candidate: list[dict[str, Any]] = candidate,
                candidate_tools: list[dict[str, Any]] = candidate_tools,
            ) -> ua.InputEstimate:
                return ua.estimate_input(
                    candidate,
                    scope=digest("request" if compatible else "changed"),
                    compatibility_scope=digest("route" if compatible else "changed"),
                    anchor=anchor,
                    tools=candidate_tools,
                    fallback_tokens=lambda: ua.estimate_full_input(
                        candidate,
                        candidate_tools,
                        unknown_token_reserve=40_000,
                        anchor=anchor if compatible else None,
                        cache=prepared,
                    ),
                    unknown_token_reserve=40_000,
                    fallback_threshold=32_000,
                    cache=prepared,
                )

            assert assess(cache) == assess(None)


def test_cost_cache_keeps_duplicate_occurrences_and_new_output_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"role": "assistant", "reasoning_details": [{"encrypted_content": "opaque"}]}
    cache = InputBudgetCache()
    original = ua._estimate_delta
    calls = 0

    def counted(messages: list[dict[str, Any]], anchor: UsageAnchor) -> int | None:
        nonlocal calls
        calls += 1
        return original(messages, anchor)

    monkeypatch.setattr(ua, "_estimate_delta", counted)
    empty = UsageAnchor("", "", 0, 0)
    for _ in range(20):
        assert (
            ua.estimate_full_input([payload, payload], None, unknown_token_reserve=800, cache=cache)
            == 800
        )
    assert calls == 2  # Header and opaque field, once each, despite 40 occurrences.
    bound = replace(
        empty,
        output_reasoning_digest=digest(payload["reasoning_details"]),
        output_reasoning_tokens=7,
    )
    assert (
        ua.estimate_full_input(
            [payload, payload], None, unknown_token_reserve=800, anchor=bound, cache=cache
        )
        == 14
    )
    bound = replace(bound, output_reasoning_tokens=11)
    assert (
        ua.estimate_full_input(
            [payload, payload], None, unknown_token_reserve=800, anchor=bound, cache=cache
        )
        == 22
    )


@pytest.mark.parametrize("native", [False, True])
def test_candidate_images_read_once_but_final_and_next_search_refresh(
    tmp_path: Path,
    native: bool,
) -> None:
    data = b"local frozen image"
    sha = hashlib.sha256(data).hexdigest()
    ref = AttachmentRef(sha, "game.png", "image", "image/png", len(data), sha)
    reads = 0
    missing = False

    def open_asset(_: str) -> BytesIO:
        nonlocal reads
        reads += 1
        if missing:
            raise FileNotFoundError
        return BytesIO(data)

    adapter = MultimodalInputAdapter(open_asset=open_asset)
    client = LLMClient(
        ProviderSpec(
            model="openai/kimi-k3",
            api_key="fixture",
            context_window=800000,
            attachment_capabilities=KIMI_MULTIMODAL_CAPABILITIES,
            responses=ResponsesRoute(model="fixture", api_base="https://unused.invalid")
            if native
            else None,
        )
    )
    conv, _, _ = _make_conversation(
        tmp_path, fake_llm=client, context_manager=SummarizingContextManager()
    )
    conv._message_budget_renderer = adapter.render_budget_message
    conv._message_renderer = lambda *_: pytest.fail("candidate invoked general renderer")
    messages = [Message(role="tool", content="look", meta={"tool_call_id": "call"}, images=(ref,))]
    expected = conv._estimate_context_input(messages, None)
    reads = 0
    assess = conv._candidate_context_assessor(None)
    for _ in range(20):
        assert assess(messages) == expected
    assert reads == 1
    changed = [replace(messages[0], content="new text with same UUID")]
    assert assess(changed) == conv._estimate_context_input(changed, None)
    assert reads == 3
    missing = True
    final = conv._estimate_context_input(messages, None)
    assert final != expected
    assert conv._candidate_context_assessor(None)(messages) == final
    assert reads == 5
    # A route modality change within the same scope must invalidate rendered input.
    client.tool_images_rejected = True
    assert assess(messages) == conv._estimate_context_input(messages, None)
    assert reads == 5


def test_candidate_config_change_invalidates_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .test_context_usage_anchor import MeasuredLLM

    conv, _, _ = _make_conversation(
        tmp_path, fake_llm=MeasuredLLM(), context_manager=SummarizingContextManager()
    )
    count = 0

    def render(message: Message, _: Any) -> dict[str, Any]:
        nonlocal count
        count += 1
        return message.to_openai()

    conv._message_budget_renderer = render
    messages = [Message(role="user", content="same history")]
    assess = conv._candidate_context_assessor(None)
    assess(messages)
    assess(messages)
    assert count == 1
    conv._run_id_override = "another-run"
    assess(messages)
    assert count == 2
    conv._usage_anchor = UsageAnchor(digest("scope"), digest("prefix"), 1, 100)
    assess(messages)
    assert count == 3
    runtime = conv._build_runtime_context(history=messages)
    assert runtime.candidate_assessment_factory is not None
    runtime.candidate_assessment_factory()(messages)
    assert count == 4
    config = conv._active_model_config()
    monkeypatch.setattr(
        conv, "_active_model_config", lambda: replace(config, reasoning_preset="off")
    )
    assess(messages)
    assert count == 5
    monkeypatch.setattr(conv, "_active_model_config", lambda: replace(config, model_id="changed"))
    assess(messages)
    assert count == 6
