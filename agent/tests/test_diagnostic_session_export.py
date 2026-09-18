from __future__ import annotations

import io
import json
from typing import Any

import pytest
from agent.sessions import diagnostic_export as export


def event(kind: str, identity: str, **payload: object) -> dict[str, object]:
    return {
        "type": kind,
        "uuid": identity,
        "ts": "2026-09-14T00:00:00Z",
        "payload": payload,
        "meta": {},
    }


def collect(events: list[dict[str, object]], limit: int | None = 1) -> dict[str, Any]:
    raw = b"\n".join(json.dumps(e).encode() for e in events) + b"\n"
    return export.export_prefix(io.BytesIO(raw), len(raw), limit)


def test_recent_turn_keeps_tool_pair_without_full_history() -> None:
    result = collect(
        [
            event("session_meta", "meta", session_id="s"),
            event("user_message", "old", content="old-private-text"),
            event("user_message", "new", content="new"),
            event("tool_call_request", "req", tool_call_id="call"),
            event("tool_call_result", "res", tool_call_id="call", content="failure"),
        ]
    )
    assert [e["uuid"] for e in result["events"]] == ["meta", "new", "req", "res"]
    assert "old-private-text" not in json.dumps(result)
    assert result["coverage"]["includedTurns"] == 1
    assert result["coverage"]["completeWithinRequestedRange"]


def test_rewrite_counts_effective_turn_and_keeps_range_evidence() -> None:
    result = collect(
        [
            event("session_meta", "meta"),
            event("user_message", "u1"),
            event("user_message", "old"),
            event("assistant_message", "bad"),
            event("turn_rewrite", "rewrite", inactive_event_uuids=["old", "bad"]),
            event("user_message", "replacement"),
            event("assistant_message", "new"),
        ],
        2,
    )
    assert result["coverage"]["includedTurns"] == 2
    assert [e["uuid"] for e in result["events"]] == [
        "meta",
        "u1",
        "old",
        "bad",
        "rewrite",
        "replacement",
        "new",
    ]


def test_internal_snapshots_cannot_reintroduce_old_history() -> None:
    result = collect(
        [
            event("session_meta", "meta"),
            event("user_message", "u"),
            event("rollout_commit", "snapshot", transcript=["private-old-content"]),
            event("provider_reasoning_state", "reasoning", data="private-reasoning"),
        ]
    )
    assert "private-" not in json.dumps(result)
    assert result["events"][-1]["payload"]["redacted"]


def test_all_empty_in_progress_and_system_turns() -> None:
    assert collect([event("session_meta", "meta")])["coverage"]["includedTurns"] == 0
    result = collect(
        [event("session_meta", "meta"), event("user_message", "u"), event("system_trigger", "s")],
        None,
    )
    assert result["coverage"]["includedTurns"] == 2
    assert result["coverage"]["mode"] == "all"
    assert result["coverage"]["completeWithinRequestedRange"]


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5])
def test_invalid_range_rejected(limit: object) -> None:
    with pytest.raises(ValueError):
        export.export_prefix(io.BytesIO(), 0, limit)  # type: ignore[arg-type]


def test_reverse_chunks_match_forward_lines() -> None:
    records = [b"a" * 123, b"b" * (export.CHUNK_BYTES * 2 + 10), b"c" * 32]
    raw = b"\r\n".join(records) + b"\r\n"
    metrics = {"readBytes": 0}
    actual = list(export._reverse_lines(io.BytesIO(raw), len(raw), metrics))
    assert [line.rstrip(b"\r") for _, line, large in actual if not large] == list(reversed(records))


def test_recent_read_cost_independent_of_old_history() -> None:
    tail = [event("user_message", "last"), event("assistant_message", "answer")]
    for count in (100, 1000):
        result = collect(
            [
                event("session_meta", "meta"),
                *[event("user_message", str(i), content="x" * 2000) for i in range(count)],
                *tail,
            ]
        )
        assert result["coverage"]["readBytes"] < 2 * export.CHUNK_BYTES
        assert result["coverage"]["eventBytes"] < 1000


def test_large_records_and_total_output_are_not_truncated() -> None:
    content = "x" * (5 * 1024 * 1024)
    result = collect(
        [event("session_meta", "meta"), event("user_message", "u")]
        + [event("tool_call_result", str(i), content=content) for i in range(4)],
        None,
    )
    assert result["coverage"]["completeWithinRequestedRange"]
    assert result["coverage"]["eventBytes"] > 16 * 1024 * 1024
    assert all(e["payload"]["content"] == content for e in result["events"][2:])
    assert result["coverage"]["limits"] == {
        "scanBytes": None,
        "eventBytes": None,
        "exportBytes": None,
    }


@pytest.mark.parametrize("limit", [None, 100])
def test_internal_snapshots_do_not_exhaust_scan_before_requested_turns(limit: int | None) -> None:
    # Reproduce a long session where internal records consume the old 64 MiB budget.
    result = collect(
        [event("session_meta", "meta"), event("user_message", "first")]
        + [event("rollout_commit", str(i), transcript="x" * (1024 * 1024)) for i in range(65)]
        + [event("user_message", "last"), event("assistant_message", "answer")],
        limit,
    )
    assert result["coverage"]["readBytes"] > 64 * 1024 * 1024
    assert result["coverage"]["includedTurns"] == 2
    assert result["coverage"]["completeWithinRequestedRange"]
    assert result["coverage"]["gaps"] == []
    assert result["events"][1]["uuid"] == "first"
    assert result["coverage"]["internalSnapshotCount"] == 65


def test_damaged_tail_does_not_invent_complete_turn_count() -> None:
    raw = json.dumps(event("session_meta", "meta")).encode() + b'\n{"broken"'
    result = export.export_prefix(io.BytesIO(raw), len(raw), 10)
    assert result["coverage"]["gaps"] == ["invalid_record"]
