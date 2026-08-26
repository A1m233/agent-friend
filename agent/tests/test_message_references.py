"""Quoted-text domain and model-projection regression tests."""

from __future__ import annotations

import pytest
from agent.context.budget import estimate_tokens
from agent.context.summary_prompt import render_transcript
from agent.message_references import (
    QuotedTextLocator,
    QuotedTextReference,
    QuotedTextSource,
    parse_quoted_text_references,
    render_memory_user_text,
    render_retrieval_query,
)
from agent.messages import Message


def _reference(
    *,
    reference_id: str = "ref-1",
    text: str = "先把真实证据补齐",
    role: str = "assistant",
) -> QuotedTextReference:
    return QuotedTextReference(
        reference_id=reference_id,
        quoted_text=text,
        source_role=role,  # type: ignore[arg-type]
        availability="available",
        source=QuotedTextSource(
            kind="session_message",
            message_event_uuid="event-source",
            text_block_id="block-source",
        ),
        locator=QuotedTextLocator(
            kind="text_range",
            coordinate_space="rendered_text_utf16_v1",
            start=2,
            end=11,
        ),
    )


def test_reference_round_trip_and_duplicate_rejection() -> None:
    first = _reference()
    restored = Message.from_dict(
        Message(role="user", content="为什么？", references=[first]).to_dict()
    )

    assert restored.references == [first]
    with pytest.raises(ValueError, match="reference_id 不能重复"):
        parse_quoted_text_references([first, first])


def test_model_projection_preserves_order_and_hides_ui_anchors() -> None:
    message = Message(
        role="user",
        content="这句话怎么理解？",
        references=[
            _reference(reference_id="first", text="第一段", role="assistant"),
            _reference(reference_id="second", text="第二段", role="user"),
        ],
    )

    rendered = message.to_openai()["content"]

    assert rendered.index("第一段") < rendered.index("第二段") < rendered.index("这句话怎么理解")
    assert '"source_role":"assistant"' in rendered
    assert '"source_role":"user"' in rendered
    assert "event-source" not in rendered
    assert "block-source" not in rendered
    assert "rendered_text_utf16_v1" not in rendered


def test_quote_only_context_participates_in_budget_summary_and_retrieval() -> None:
    reference = _reference(text="引用内容不能被当成当前用户的新主张")
    message = Message(role="user", content="", references=[reference])

    assert estimate_tokens([message]) > 0
    assert "引用内容不能被当成当前用户的新主张" in render_transcript([message])
    assert render_retrieval_query("", [reference]) == (
        "[引用自 assistant] 引用内容不能被当成当前用户的新主张"
    )
    memory_text = render_memory_user_text("", [reference])
    assert "引用了 1 条历史内容" in memory_text
    assert "引用内容不能被当成当前用户的新主张" not in memory_text


def test_unavailable_external_reference_has_no_fake_snapshot() -> None:
    reference = QuotedTextReference(
        reference_id="missing",
        quoted_text=None,
        source_role="unknown",
        availability="unavailable",
        source=QuotedTextSource(kind="external_message", external_source_key="opaque-key"),
        locator=QuotedTextLocator(kind="whole_message"),
    )

    rendered = Message(role="user", content="继续", references=[reference]).to_openai()["content"]

    assert '"availability":"unavailable"' in rendered
    assert '"text"' not in rendered
    assert "opaque-key" not in rendered


def test_text_range_rejects_external_or_missing_text_block_source() -> None:
    with pytest.raises(ValueError, match="必须指向 session_message"):
        QuotedTextReference(
            reference_id="bad",
            quoted_text="bad",
            source_role="unknown",
            availability="available",
            source=QuotedTextSource(kind="external_message", external_source_key="opaque"),
            locator=QuotedTextLocator(
                kind="text_range",
                coordinate_space="rendered_text_utf16_v1",
                start=0,
                end=3,
            ),
        )
