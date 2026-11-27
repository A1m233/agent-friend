"""Range-selected diagnostic projection of a pinned JSONL prefix, without byte caps."""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from tempfile import TemporaryFile
from time import perf_counter
from typing import Any, BinaryIO

from .events import ROLLOUT_EVENT_TYPES
from .query import CancelCheck, check_cancel

CHUNK_BYTES = 64 * 1024
TURN_TYPES = {"user_message", "system_trigger"}
SUMMARY_TYPES = ROLLOUT_EVENT_TYPES | {"model_context", "skill_catalog_state"}


class SnapshotReadError(OSError):
    """Only source read failures may produce a partial diagnostic projection."""


def _reverse_lines(
    source: BinaryIO, length: int, metrics: dict[str, int], cancel_check: CancelCheck = None
) -> Iterator[tuple[int, bytes, bool]]:
    position = length
    # Keep fragments separately: concatenating a large record on every chunk is quadratic.
    pending: list[bytes] = []
    while position:
        check_cancel(cancel_check)
        size = min(CHUNK_BYTES, position)
        position -= size
        try:
            source.seek(position)
            chunk = source.read(size)
        except OSError as exc:
            raise SnapshotReadError(str(exc)) from exc
        metrics["readBytes"] += len(chunk)
        if len(chunk) != size:
            raise SnapshotReadError("diagnostic snapshot truncated")
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


def stream_prefix(
    source: BinaryIO,
    length: int,
    turn_limit: int | None,
    cancel_check: CancelCheck = None,
) -> Iterator[dict[str, Any]]:
    """Project a pinned prefix with disk-backed reverse ordering, one event in memory."""
    if turn_limit is not None and (type(turn_limit) is not int or not 1 <= turn_limit <= 100):
        raise ValueError("turn_limit must be an integer from 1 to 100, or null")
    started = perf_counter()
    metrics = {"readBytes": 0, "scanStart": length}
    inactive: set[str] = set()
    gaps: set[str] = set()
    turns = total = summary_count = 0
    reached_boundary = False
    start_offset = length
    check_cancel(cancel_check)
    source.seek(0)
    # Session metadata is an ordinary first record. Reads remain cancellation-aware.
    head_parts: list[bytes] = []
    remaining = length
    while remaining:
        check_cancel(cancel_check)
        chunk = source.readline(min(CHUNK_BYTES, remaining))
        if not chunk:
            break
        head_parts.append(chunk)
        remaining -= len(chunk)
        if chunk.endswith(b"\n"):
            break
    head = b"".join(head_parts)
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
    if meta is not None:
        yield {"kind": "event", "event": meta}
    first_event = last_event = None
    # Each projected record is followed by its byte length. Walk backwards after
    # scanning to emit chronological JSON without a whole-session event list.
    with TemporaryFile() as spool:
        last_progress = 0
        try:
            for offset, raw, _ in _reverse_lines(source, length, metrics, cancel_check):
                check_cancel(cancel_check)
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
                encoded = json.dumps(event, ensure_ascii=False).encode("utf-8")
                spool.write(encoded)
                spool.write(struct.pack("<Q", len(encoded)))
                total += len(encoded)
                first_event = event.get("uuid")
                if last_event is None:
                    last_event = first_event
                if metrics["readBytes"] - last_progress >= 1024 * 1024:
                    last_progress = metrics["readBytes"]
                    yield {"kind": "progress", "readBytes": last_progress}
                if kind in TURN_TYPES and event.get("uuid") not in inactive:
                    turns += 1
                    if turn_limit is not None and turns == turn_limit:
                        reached_boundary = True
                        break
        except SnapshotReadError:
            gaps.add("snapshot_truncated")
        if not reached_boundary and not gaps:
            gaps.add("metadata_unavailable")
        end = spool.tell()
        while end:
            check_cancel(cancel_check)
            spool.seek(end - 8)
            size = struct.unpack("<Q", spool.read(8))[0]
            end -= size + 8
            spool.seek(end)
            yield {"kind": "event", "event": json.loads(spool.read(size))}
    yield {
        "kind": "coverage",
        "session_id": meta.get("payload", {}).get("session_id") if meta else None,
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
            "firstEvent": first_event,
            "lastEvent": last_event,
            "gaps": sorted(gaps),
            "internalSnapshotCount": summary_count,
            "exclusions": ["internal_snapshot_bodies", "provider_reasoning_payloads"],
            "scope": "persisted-prefix; external references are not expanded",
            "limits": {"scanBytes": None, "eventBytes": None, "exportBytes": None},
        },
    }


def export_prefix(
    source: BinaryIO, length: int, turn_limit: int | None, cancel_check: CancelCheck = None
) -> dict[str, Any]:
    """Compatibility adapter for existing in-process/JSON consumers."""
    events: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for record in stream_prefix(source, length, turn_limit, cancel_check):
        if record["kind"] == "event":
            events.append(record["event"])
        elif record["kind"] == "coverage":
            result = {key: value for key, value in record.items() if key != "kind"}
    return {**result, "events": events}
