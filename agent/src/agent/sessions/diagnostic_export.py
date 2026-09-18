"""Range-selected diagnostic projection of a pinned JSONL prefix, without byte caps."""

from __future__ import annotations

import json
from collections.abc import Iterator
from time import perf_counter
from typing import Any, BinaryIO

from .events import ROLLOUT_EVENT_TYPES

CHUNK_BYTES = 64 * 1024
TURN_TYPES = {"user_message", "system_trigger"}
SUMMARY_TYPES = ROLLOUT_EVENT_TYPES | {"model_context", "skill_catalog_state"}


def _reverse_lines(
    source: BinaryIO, length: int, metrics: dict[str, int]
) -> Iterator[tuple[int, bytes, bool]]:
    position = length
    # Keep fragments separately: concatenating a large record on every chunk is quadratic.
    pending: list[bytes] = []
    while position:
        size = min(CHUNK_BYTES, position)
        position -= size
        source.seek(position)
        chunk = source.read(size)
        metrics["readBytes"] += len(chunk)
        if len(chunk) != size:
            raise OSError("diagnostic snapshot truncated")
        pieces = chunk.split(b"\n")
        # The rightmost piece continues the partial record from the next chunk.
        pending.append(pieces.pop())
        if pieces:
            tail = b"".join(reversed(pending))
            offset = position + sum(len(p) + 1 for p in pieces)
            if tail.strip():
                yield offset, tail, False
            for index in range(len(pieces) - 1, 0, -1):
                offset -= len(pieces[index]) + 1
                if pieces[index].strip():
                    yield offset, pieces[index], False
            pending = [pieces[0]]
    tail = b"".join(reversed(pending))
    if tail.strip():
        yield 0, tail, False
    metrics["scanStart"] = position


def _project(event: dict[str, Any]) -> dict[str, Any]:
    kind = event.get("type")
    if kind in SUMMARY_TYPES or kind == "provider_reasoning_state":
        # Rollout snapshots can embed the entire older conversation or private reasoning.
        event = {key: event[key] for key in ("type", "uuid", "ts") if key in event} | {
            "payload": {"redacted": True, "diagnosticSummaryOnly": True},
            "meta": {},
        }
    return event


def export_prefix(source: BinaryIO, length: int, turn_limit: int | None) -> dict[str, Any]:
    if turn_limit is not None and (type(turn_limit) is not int or not 1 <= turn_limit <= 100):
        raise ValueError("turn_limit must be an integer from 1 to 100, or null")
    started = perf_counter()
    metrics = {"readBytes": 0, "scanStart": length}
    events: list[dict[str, Any]] = []
    inactive: set[str] = set()
    gaps: set[str] = set()
    turns = 0
    total = 0
    reached_boundary = False
    start_offset = length
    summary_count = 0
    # Metadata is read independently; no forward full-session projection is created.
    source.seek(0)
    head = source.readline(length)
    try:
        meta = json.loads(head)
        if (
            not isinstance(meta, dict)
            or meta.get("type") != "session_meta"
            or not isinstance(meta.get("payload"), dict)
        ):
            raise ValueError("missing metadata")
    except (ValueError, UnicodeDecodeError, RecursionError):
        meta = None
        gaps.add("metadata_unavailable")
    try:
        for offset, raw, _ in _reverse_lines(source, length, metrics):
            start_offset = offset
            try:
                event = json.loads(raw)
                if (
                    not isinstance(event, dict)
                    or not isinstance(event.get("payload"), dict)
                    or not isinstance(event.get("type"), str)
                    or not isinstance(event.get("uuid"), str)
                ):
                    raise ValueError("invalid event")
            except (ValueError, UnicodeDecodeError, RecursionError):
                gaps.add("invalid_record")
                # Corruption could hide a turn boundary: do not invent an exact count.
                break
            kind = event.get("type")
            if kind == "session_meta":
                reached_boundary = True
                break
            if kind == "turn_rewrite":
                ids = event["payload"].get("inactive_event_uuids", [])
                if isinstance(ids, list):
                    inactive.update(item for item in ids if isinstance(item, str))
            event = _project(event)
            if kind in SUMMARY_TYPES:
                summary_count += 1
            size = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
            total += size
            events.append(event)
            if kind in TURN_TYPES and event.get("uuid") not in inactive:
                turns += 1
                if turn_limit is not None and turns == turn_limit:
                    reached_boundary = True
                    break
    except OSError:
        gaps.add("snapshot_truncated")
    if not reached_boundary and not gaps:
        gaps.add("metadata_unavailable")
    events.reverse()
    if meta is not None:
        events.insert(0, meta)
    payload = meta.get("payload", {}) if meta else {}
    return {
        "session_id": payload.get("session_id"),
        "events": events,
        "coverage": {
            "schemaVersion": 2,
            "mode": "all" if turn_limit is None else "recent",
            "requestedTurns": turn_limit,
            "includedTurns": turns,
            "completeWithinRequestedRange": not gaps,
            "earlierHistoryOmitted": start_offset > len(head),
            "startOffset": start_offset,
            "snapshotBytes": length,
            "readBytes": metrics["readBytes"] + len(head),
            "eventBytes": total,
            "durationMs": round((perf_counter() - started) * 1000),
            "firstEvent": events[1].get("uuid") if len(events) > 1 else None,
            "lastEvent": events[-1].get("uuid") if events else None,
            "gaps": sorted(gaps),
            "internalSnapshotCount": summary_count,
            "exclusions": ["internal_snapshot_bodies", "provider_reasoning_payloads"],
            "scope": "persisted-prefix; external references are not expanded",
            "limits": {
                "scanBytes": None,
                "eventBytes": None,
                "exportBytes": None,
            },
        },
    }
