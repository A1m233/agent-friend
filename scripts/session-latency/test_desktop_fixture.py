"""Offline synthetic histories exercise the public complete-turn paging contract."""

from __future__ import annotations

import runpy
from pathlib import Path

fixture = runpy.run_path(str(Path(__file__).with_name("desktop_fixture.py")))


def test_long_fixture_has_three_complete_pages_and_unabridged_tool_results(tmp_path: Path) -> None:
    events, row = fixture["synthetic_events"]("long", "offline/fixture")
    expected = row["expected"]
    assert expected["turn_count"] == 65
    assert expected["latest"]["tool_count"] == 10
    assert expected["latest"]["answer_bytes"] > 8192
    assert expected["latest"]["message_uuid"] == expected["latest"]["tools"][0]["request_uuid"]
    path = tmp_path / f"{row['session_id']}.jsonl"
    path.write_text("".join(event.to_jsonl() + "\n" for event in events), encoding="utf-8")
    verified = fixture["verify_synthetic"](tmp_path, row)
    assert verified["page_turn_counts"] == [30, 30, 5]
    assert verified["full_tool_pairs"] == 10
    assert verified["unloaded_reference_sources"] == 2


def test_latest_user_references_earliest_user_and_assistant_text() -> None:
    from agent.message_references import parse_quoted_text_references

    events, row = fixture["synthetic_events"]("long", "offline/fixture")
    expected = row["expected"]
    by_id = {event.uuid: event for event in events}
    references = parse_quoted_text_references(
        by_id[expected["latest"]["user_uuid"]].payload["references"]
    )
    assert [reference.source_role for reference in references] == ["user", "assistant"]
    assert [reference.source.message_event_uuid for reference in references] == [
        expected["oldest"]["user_uuid"],
        expected["oldest"]["answer_uuid"],
    ]
    for reference in references:
        assert reference.source.message_event_uuid is not None
        assert reference.quoted_text is not None
        assert reference.availability == "available"
        assert reference.source.kind == "session_message"
        assert reference.source.text_block_id == reference.source.message_event_uuid
        source = by_id[reference.source.message_event_uuid]
        assert reference.locator.coordinate_space == "rendered_text_utf16_v1"
        assert reference.locator.start == 0
        assert reference.locator.end == len(reference.quoted_text)
        assert source.payload["content"].startswith(reference.quoted_text)


def test_short_fixture_is_separate_and_has_no_tools(tmp_path: Path) -> None:
    events, row = fixture["synthetic_events"]("short", "offline/fixture")
    assert row["session_id"] != fixture["identity"]("long")
    assert row["expected"]["turn_count"] == 2
    path = tmp_path / f"{row['session_id']}.jsonl"
    path.write_text("".join(event.to_jsonl() + "\n" for event in events), encoding="utf-8")
    assert fixture["verify_synthetic"](tmp_path, row)["page_turn_counts"] == [2]
