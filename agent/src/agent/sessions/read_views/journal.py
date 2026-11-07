"""Read only requested business records, without restoring the model journal.

Legacy JSONL needs no conversion. Reverse traversal uses byte offsets and only
decodes business records in the requested window. Internal records are skipped
without allocating their potentially large bodies. This reader deliberately does
not certify the integrity of unvisited history; execution retains that contract.
"""

from __future__ import annotations

import mmap
import re
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import sleep

from llm_providers import (
    ModelRuntimeConfig,
    default_model_runtime_config,
    validate_model_runtime_config,
)

from ..continuation import ContinuationTarget, continuation_target
from ..errors import SessionCorruptError
from ..events import ALLOWED_EVENT_TYPES, ROLLOUT_EVENT_TYPES, Event
from ..query import CancelCheck, check_cancel
from ..session import Session
from ..store_types import SourceVersion
from .display import display_event

_TYPE = re.compile(rb'^\s*\{\s*"type"\s*:\s*"([a-z_]+)"')
_CONFIG = {"model_change", "model_config_change", "persona_change", "channel_change"}
_TURNS = {"user_message", "system_trigger"}


def _record_starts(skip: frozenset[str]) -> re.Pattern[bytes]:
    # Only skip the canonical writer's exact prefix. Noncanonical records
    # (including legacy whitespace or reordered fields) take the normal parser.
    kinds = b"|".join(kind.encode("ascii") for kind in sorted(skip))
    return re.compile(rb'\n(?!\{"type": "(?:' + kinds + rb')")')


_HISTORY_STARTS = _record_starts(ROLLOUT_EVENT_TYPES)
_CONFIG_STARTS = _record_starts(ALLOWED_EVENT_TYPES - _CONFIG - {"turn_rewrite"})


def valid_configuration(event: Event) -> bool:
    target = event.payload.get("to")
    if event.type == "channel_change":
        return target in ("text", "voice")
    if event.type == "persona_change":
        return isinstance(target, str)
    if event.type == "model_change":
        if not isinstance(target, str):
            return False
        try:
            default_model_runtime_config(target)
            return True
        except (ValueError, TypeError):
            return False
    if event.type == "model_config_change" and isinstance(target, dict):
        model, preset = target.get("model"), target.get("reasoningPreset")
        if not isinstance(model, str) or not isinstance(preset, str):
            return False
        try:
            validate_model_runtime_config(ModelRuntimeConfig(model, preset))
            return True
        except ValueError:
            try:
                default_model_runtime_config(model)
                return True
            except ValueError:
                return False
    return False


@dataclass(frozen=True)
class HistoryWindow:
    session_id: str
    active_events: tuple[Event, ...]
    history_cursor: str | None
    history_epoch: str
    recovery: ContinuationTarget | None
    read_only: bool
    # Raw offsets are used only to extend the contiguous, persisted legacy suffix.
    positions: tuple[int, ...] = ()


def epoch(version: SourceVersion) -> str:
    return "tail-" + sha256(repr(version).encode()).hexdigest()[:24]


def records_reverse(
    data: mmap.mmap,
    end: int,
    cancel_check: CancelCheck,
    starts: re.Pattern[bytes] = _HISTORY_STARTS,
) -> Iterator[tuple[int, int, str | None]]:
    # Scan for candidate line starts in bounded blocks. The regex crosses all
    # physical bytes, but does not dispatch a Python iteration for each rollout
    # record. A header crossing the block edge is conservatively included.
    upper = end
    blocks = 0
    while upper:
        check_cancel(cancel_check)
        if blocks and blocks % 8 == 0:
            sleep(0)
        lower = max(0, upper - 256 * 1024)
        offsets = [match.end() for match in starts.finditer(data, lower, upper)]
        if lower == 0:
            offsets.insert(0, 0)
        for start in reversed(offsets):
            if start >= end:
                continue
            stop = data.find(b"\n", start, end)
            if stop < 0:
                stop = end
            if stop == start:
                continue
            match = _TYPE.match(data[start : min(stop, start + 256)])
            yield start, stop, match[1].decode() if match else None
        upper = lower
        blocks += 1


def _event(data: mmap.mmap, start: int, stop: int) -> Event:
    try:
        return Event.from_jsonl(data[start:stop].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SessionCorruptError("会话历史包含无效 UTF-8") from exc


def _head(data: mmap.mmap) -> Session:
    end = data.find(b"\n")
    return Session.from_events([_event(data, 0, end if end >= 0 else len(data))])


def read_window(
    path: Path,
    version: SourceVersion,
    *,
    turn_limit: int,
    before: str | None = None,
    history_epoch: str | None = None,
    cancel_check: CancelCheck = None,
) -> HistoryWindow:
    if turn_limit < 1:
        raise ValueError("turn_limit must be positive")
    current_epoch = epoch(version)
    if history_epoch is not None and history_epoch != current_epoch:
        raise ValueError("History changed; reload the latest page")
    boundary = version[2]
    if before is not None:
        try:
            prefix, raw_offset = before.split(":", 1)
            if prefix != current_epoch:
                raise ValueError()
            boundary = int(raw_offset)
            if not 0 < boundary < version[2]:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise ValueError("history cursor is no longer available") from exc
    retained: list[tuple[int, Event]] = []
    inactive: set[str] = set()
    latest: list[Event] = []
    latest_complete = False
    turns = 0
    has_older = False
    boundary_seen = before is None
    with path.open("rb") as source:
        if version[2] == 0:
            raise SessionCorruptError("会话文件为空")
        with mmap.mmap(source.fileno(), version[2], access=mmap.ACCESS_READ) as data:
            session = _head(data)
            for start, stop, kind in records_reverse(data, version[2], cancel_check):
                if kind in ROLLOUT_EVENT_TYPES:
                    continue
                event = _event(data, start, stop)
                if event.type in ROLLOUT_EVENT_TYPES:
                    continue
                if event.type == "turn_rewrite":
                    inactive.update(
                        uid
                        for uid in event.payload.get("inactive_event_uuids", [])
                        if isinstance(uid, str)
                    )
                    continue
                if event.uuid in inactive:
                    continue
                event = display_event(event)
                if not latest_complete:
                    latest.append(event)
                    latest_complete = event.type in _TURNS
                if start >= boundary:
                    if start == boundary and event.type in _TURNS:
                        boundary_seen = True
                    continue
                if event.type in _TURNS:
                    turns += 1
                    if turns > turn_limit:
                        has_older = True
                        break
                retained.append((start, event))
            if not boundary_seen:
                raise ValueError("history cursor must point to a complete turn boundary")
    # Records between the preceding turn and this page's first input belong to
    # the preceding turn. Keep metadata at file start only on the oldest page.
    retained.reverse()
    if has_older:
        first_turn = next(i for i, (_, event) in enumerate(retained) if event.type in _TURNS)
        retained = retained[first_turn:]
    from ...errors import AgentError

    try:
        recovery = continuation_target(list(reversed(latest)))
    except AgentError:
        recovery = None
    return HistoryWindow(
        session.session_id,
        tuple(event for _, event in retained),
        f"{current_epoch}:{retained[0][0]}" if has_older else None,
        current_epoch,
        recovery,
        session.origin_kind == "inner_world",
        tuple(start for start, _ in retained),
    )


def read_configuration(
    path: Path, version: SourceVersion, cancel_check: CancelCheck = None
) -> Session:
    """Legacy bootstrap: parse configuration only, never restore business/model history."""
    selected: list[Event] = []
    inactive: set[str] = set()
    found: set[str] = set()
    with path.open("rb") as source:
        if not version[2]:
            raise SessionCorruptError("会话文件为空")
        with mmap.mmap(source.fileno(), version[2], access=mmap.ACCESS_READ) as data:
            session = _head(data)
            for start, stop, kind in records_reverse(
                data, version[2], cancel_check, _CONFIG_STARTS
            ):
                if kind is not None and kind not in _CONFIG | {"turn_rewrite"}:
                    continue
                event = _event(data, start, stop)
                if event.type == "turn_rewrite":
                    inactive.update(
                        uid
                        for uid in event.payload.get("inactive_event_uuids", [])
                        if isinstance(uid, str)
                    )
                elif (
                    event.type in _CONFIG
                    and event.uuid not in inactive
                    and valid_configuration(event)
                ):
                    category = "model" if event.type.startswith("model_") else event.type
                    if category not in found:
                        selected.append(event)
                        found.add(category)
                if len(found) == 3:
                    break
    session.events.extend(reversed(selected))
    return session
