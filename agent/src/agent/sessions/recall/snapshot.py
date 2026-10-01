"""Only retain fields consumed by recall; preserve original event positions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from ..events import ROLLOUT_EVENT_TYPES, Event
from ..query import CancelCheck, check_cancel, query_session
from ..store_types import SessionLoadDiagnostics, SourceVersion

if TYPE_CHECKING:
    from ..store import SessionStore


@dataclass(frozen=True)
class SessionRecallSnapshot:
    session_id: str
    events: tuple[Event, ...]
    raw_event_indices: Mapping[str, int]
    raw_event_count: int
    source_version: SourceVersion | None
    diagnostics: SessionLoadDiagnostics
    created_at: datetime
    updated_at: datetime


def conversation_event(event: Event) -> Event | None:
    if event.type not in {"user_message", "assistant_message"}:
        return None
    if event.type == "assistant_message" and event.payload.get("partial"):
        return None
    content = event.payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    persona = event.meta.get("persona_id")
    return Event(
        type=event.type,
        uuid=event.uuid,
        ts=event.ts,
        payload={"content": content},
        meta={"persona_id": persona} if isinstance(persona, str) and persona else {},
    )


def inactive_uuids(event: Event) -> tuple[str, ...]:
    raw = event.payload.get("inactive_event_uuids") if event.type == "turn_rewrite" else None
    return tuple(item for item in raw if isinstance(item, str)) if isinstance(raw, list) else ()


class RecallBuilder:
    def __init__(self) -> None:
        self.positions: dict[str, int] = {}
        self.events: list[Event] = []
        self.inactive: set[str] = set()
        self.head: Event | None = None
        self.updated_at: datetime | None = None

    def add(self, position: int, event: Event) -> None:
        if self.head is None:
            # The store validates the full session_meta; retain no unrelated payload.
            self.head = Event(type=event.type, uuid=event.uuid, ts=event.ts, payload={})
            self.updated_at = event.ts
        if event.type in ROLLOUT_EVENT_TYPES:
            return
        self.positions[event.uuid] = position
        self.inactive.update(inactive_uuids(event))
        if event.type in {"user_message", "assistant_message"}:
            self.updated_at = event.ts
        lean = conversation_event(event)
        if lean is not None:
            self.events.append(lean)

    def snapshot(
        self,
        *,
        count: int,
        version: SourceVersion | None,
        diagnostics: SessionLoadDiagnostics,
    ) -> SessionRecallSnapshot:
        assert self.head is not None and self.updated_at is not None
        return SessionRecallSnapshot(
            session_id=self.head.uuid,
            events=tuple(event for event in self.events if event.uuid not in self.inactive),
            raw_event_indices=MappingProxyType(self.positions),
            raw_event_count=count,
            source_version=version,
            diagnostics=diagnostics,
            created_at=self.head.ts,
            updated_at=self.updated_at,
        )


def query_session_recall(
    store: SessionStore,
    session_id: str,
    *,
    cancel_check: CancelCheck = None,
) -> SessionRecallSnapshot:
    check_cancel(cancel_check)
    method = getattr(store, "query_recall", None)
    if callable(method):
        return cast(SessionRecallSnapshot, method(session_id, cancel_check=cancel_check))
    source = query_session(store, session_id, cancel_check=cancel_check)
    builder = RecallBuilder()
    for event in source.events:
        check_cancel(cancel_check)
        builder.add(source.raw_event_indices[event.uuid], event)
    return builder.snapshot(
        count=source.raw_event_count,
        version=source.source_version,
        diagnostics=source.load_diagnostics,
    )
