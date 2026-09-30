"""Read-only session projections and optional store capability adapters.

The JSONL store owns freshness. A query snapshot is deliberately not a Session:
it has no mutation or execution methods and omits internal rollout payloads.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from functools import cached_property
from hashlib import sha256
from threading import Lock
from time import perf_counter
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from llm_providers import ModelRuntimeConfig

from .events import ROLLOUT_EVENT_TYPES, Event
from .query_observation import SessionQueryObservation
from .session import Channel, Session
from .store_types import SessionLoadDiagnostics
from .store_types import SourceVersion as SourceVersion

if TYPE_CHECKING:
    from .store import SessionStore

CancelCheck = Callable[[], None] | None


def check_cancel(cancel_check: CancelCheck) -> None:
    if cancel_check is not None:
        cancel_check()


class _ReadOnlyDict(dict[str, Any]):
    def _immutable(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("query event data is read-only")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable

    def __deepcopy__(self, memo: dict[int, object]) -> _ReadOnlyDict:
        return self


class _ReadOnlyList(list[Any]):
    def _immutable(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("query event data is read-only")

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = _immutable
    reverse = sort = __iadd__ = __imul__ = _immutable

    def __deepcopy__(self, memo: dict[int, object]) -> _ReadOnlyList:
        return self


def _freeze(value: Any) -> Any:
    if isinstance(value, (_ReadOnlyDict, _ReadOnlyList)):
        return value
    if isinstance(value, dict):
        return _ReadOnlyDict((key, _freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return _ReadOnlyList(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class SessionActivity:
    session_id: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SessionQuerySnapshot:
    session_id: str
    created_at: datetime
    initial_title: str
    initial_persona: str
    initial_model: str
    initial_reasoning_preset: str | None
    events: tuple[Event, ...]
    active_events: tuple[Event, ...]
    raw_event_indices: Mapping[str, int]
    raw_event_count: int
    load_diagnostics: SessionLoadDiagnostics
    source_version: SourceVersion | None
    current_persona: str
    current_persona_id: str | None
    current_model: str
    current_model_config: ModelRuntimeConfig
    current_channel: Channel
    estimated_bytes: int
    projection_epoch: str = ""

    @cached_property
    def history_epoch(self) -> str:
        rewrite = next(
            (e.uuid for e in reversed(self.events) if e.type == "turn_rewrite"), "initial"
        )
        return f"{self.projection_epoch}:{rewrite}"

    @cached_property
    def page_positions(self) -> Mapping[str, int]:
        return MappingProxyType({e.uuid: i for i, e in enumerate(self.active_events)})

    @cached_property
    def page_starts(self) -> tuple[int, ...]:
        starts = [
            i
            for i, e in enumerate(self.active_events)
            if e.type in {"user_message", "system_trigger"}
        ]
        # Metadata preceding the first turn belongs to that turn, not an extra page.
        return (0, *starts[1:]) if starts else (0,)

    def page_events(
        self, *, turn_limit: int, before: str | None = None
    ) -> tuple[tuple[Event, ...], str | None]:
        """Read complete user turns; never split an assistant's tool sequence."""
        if turn_limit < 1:
            raise ValueError("turn_limit must be positive")
        end = len(self.active_events)
        if before is not None:
            end = self.page_positions.get(before, -1)
            if end < 0:
                raise ValueError("history cursor is no longer available")
        if end == 0:
            return (), None
        starts = self.page_starts
        boundary = bisect_left(starts, end)
        if before is not None and (boundary == len(starts) or starts[boundary] != end):
            raise ValueError("history cursor must point to a complete turn boundary")
        start = starts[max(0, boundary - turn_limit)]
        return self.active_events[start:end], self.active_events[start].uuid if start else None

    @classmethod
    def build(
        cls,
        events: Iterable[tuple[int, Event]],
        *,
        raw_event_count: int,
        diagnostics: SessionLoadDiagnostics,
        source_version: SourceVersion | None,
        cancel_check: CancelCheck = None,
    ) -> SessionQuerySnapshot:
        retained: list[Event] = []
        positions: dict[str, int] = {}
        estimated_bytes = 0
        for position, event in events:
            check_cancel(cancel_check)
            if event.type in ROLLOUT_EVENT_TYPES:
                continue
            positions[event.uuid] = position
            frozen = replace(event, payload=_freeze(event.payload), meta=_freeze(event.meta))
            retained.append(frozen)
            estimated_bytes += len(event.to_jsonl().encode("utf-8")) + 256
        # Reuse the domain projection, including its legacy model/persona rules.
        session = Session.from_events(retained)
        active = tuple(session.active_events)
        check_cancel(cancel_check)
        return cls(
            session_id=session.session_id,
            created_at=session.created_at,
            initial_title=session.initial_title,
            initial_persona=session.initial_persona,
            initial_model=session.initial_model,
            initial_reasoning_preset=session.initial_reasoning_preset,
            events=tuple(retained),
            active_events=active,
            raw_event_indices=MappingProxyType(positions),
            raw_event_count=raw_event_count,
            load_diagnostics=diagnostics,
            source_version=source_version,
            current_persona=session.current_persona,
            current_persona_id=session.current_persona_id,
            current_model=session.current_model,
            current_model_config=session.current_model_config,
            current_channel=session.current_channel,
            estimated_bytes=estimated_bytes,
            projection_epoch=sha256(repr(source_version).encode("utf-8")).hexdigest()[:24],
        )

    def appended(self, events: list[Event], version: SourceVersion) -> SessionQuerySnapshot:
        if all(event.type in ROLLOUT_EVENT_TYPES for event in events):
            return replace(
                self, source_version=version, raw_event_count=self.raw_event_count + len(events)
            )
        if not any(
            event.type
            in {
                "turn_rewrite",
                "persona_change",
                "model_change",
                "model_config_change",
                "channel_change",
            }
            for event in events
        ):
            # Ordinary messages and rollout appends do not reparse/re-serialize
            # historical payloads or rebuild the model/persona projections.
            inactive = {
                uuid
                for event in self.events
                if event.type == "turn_rewrite"
                if isinstance(event.payload.get("inactive_event_uuids"), list)
                for uuid in event.payload.get("inactive_event_uuids", [])
                if isinstance(uuid, str)
            }
            positions = dict(self.raw_event_indices)
            retained: list[Event] = []
            estimated_bytes = self.estimated_bytes
            for position, event in enumerate(events, start=self.raw_event_count):
                if event.type in ROLLOUT_EVENT_TYPES:
                    continue
                positions[event.uuid] = position
                retained.append(
                    replace(event, payload=_freeze(event.payload), meta=_freeze(event.meta))
                )
                estimated_bytes += len(event.to_jsonl().encode("utf-8")) + 256
            return replace(
                self,
                events=(*self.events, *retained),
                active_events=(
                    *self.active_events,
                    *(event for event in retained if event.uuid not in inactive),
                ),
                raw_event_indices=MappingProxyType(positions),
                raw_event_count=self.raw_event_count + len(events),
                estimated_bytes=estimated_bytes,
                source_version=version,
            )
        existing = ((self.raw_event_indices[event.uuid], event) for event in self.events)
        additions = enumerate(events, start=self.raw_event_count)
        rebuilt = self.build(
            (*existing, *additions),
            raw_event_count=self.raw_event_count + len(events),
            diagnostics=self.load_diagnostics,
            source_version=version,
        )
        if not any(event.type == "turn_rewrite" for event in events):
            rebuilt = replace(rebuilt, projection_epoch=self.projection_epoch)
        return rebuilt


class SessionQueryCache:
    """Bounded immutable values, with independently cancellable per-session builders."""

    def __init__(self, *, max_entries: int = 64, max_bytes: int = 64 * 1024 * 1024) -> None:
        if max_entries < 0 or max_bytes < 0:
            raise ValueError("query cache limits must be nonnegative")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, SessionQuerySnapshot] = OrderedDict()
        self._builders: dict[str, tuple[Lock, int]] = {}
        self._lock = Lock()

    def get(
        self,
        session_id: str,
        version: SourceVersion,
        *,
        observation: SessionQueryObservation | None = None,
    ) -> SessionQuerySnapshot | None:
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None or entry.source_version != version:
                if observation is not None and observation.miss_reason is None:
                    observation.miss_reason = "absent" if entry is None else "source_changed"
                return None
            self._entries.move_to_end(session_id)
            return entry

    def put(
        self,
        snapshot: SessionQuerySnapshot,
        *,
        is_current: Callable[[], bool] = lambda: True,
        observation: SessionQueryObservation | None = None,
    ) -> None:
        with self._lock:
            if not is_current():
                if observation is not None:
                    observation.published = False
                    observation.publish_reason = "source_changed"
                return
            self._entries.pop(snapshot.session_id, None)
            if snapshot.estimated_bytes <= self._max_bytes and self._max_entries > 0:
                self._entries[snapshot.session_id] = snapshot
            if observation is not None:
                observation.published = snapshot.session_id in self._entries
                observation.publish_reason = "stored" if observation.published else "capacity"
            while (
                len(self._entries) > self._max_entries
                or sum(item.estimated_bytes for item in self._entries.values()) > self._max_bytes
            ):
                self._entries.popitem(last=False)

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._entries.pop(session_id, None)

    @contextmanager
    def building(
        self,
        session_id: str,
        cancel_check: CancelCheck,
        *,
        observation: SessionQueryObservation | None = None,
    ) -> Iterator[None]:
        started = perf_counter()
        with self._lock:
            lock, count = self._builders.get(session_id, (Lock(), 0))
            self._builders[session_id] = (lock, count + 1)
        acquired = False
        try:
            while not acquired:
                check_cancel(cancel_check)
                acquired = lock.acquire(timeout=0.05)
            if observation is not None:
                observation.wait_seconds = perf_counter() - started
            check_cancel(cancel_check)
            yield
        finally:
            if observation is not None and not acquired:
                observation.wait_seconds = perf_counter() - started
            if acquired:
                lock.release()
            with self._lock:
                _, count = self._builders[session_id]
                if count == 1:
                    self._builders.pop(session_id)
                else:
                    self._builders[session_id] = (lock, count - 1)


def load_session(
    store: SessionStore, session_id: str, *, cancel_check: CancelCheck = None
) -> Session:
    """Legacy stores keep their load signature; native stores expose cancellable_load."""
    check_cancel(cancel_check)
    if cancel_check is None:
        return store.load(session_id)
    method = getattr(store, "cancellable_load", None)
    if callable(method):
        return cast(Session, method(session_id, cancel_check=cancel_check))
    result = store.load(session_id)
    check_cancel(cancel_check)
    return result


def load_business_session(
    store: SessionStore, session_id: str, *, cancel_check: CancelCheck = None
) -> Session:
    """Mutable business facts for consumers that never restore the model journal."""
    check_cancel(cancel_check)
    method = getattr(store, "load_business", None)
    if callable(method):
        return cast(Session, method(session_id, cancel_check=cancel_check))
    return load_session(store, session_id, cancel_check=cancel_check)


def query_session(
    store: SessionStore, session_id: str, *, cancel_check: CancelCheck = None
) -> SessionQuerySnapshot:
    check_cancel(cancel_check)
    method = getattr(store, "query", None)
    if callable(method):
        return cast(SessionQuerySnapshot, method(session_id, cancel_check=cancel_check))
    session = load_session(store, session_id, cancel_check=cancel_check)
    return SessionQuerySnapshot.build(
        enumerate(session.events),
        raw_event_count=len(session.events),
        diagnostics=session.load_diagnostics,
        source_version=None,
        cancel_check=cancel_check,
    )


def list_session_activity(
    store: SessionStore,
    *,
    session_ids: Iterable[str] | None = None,
    exclude_session_ids: Iterable[str] = (),
    cancel_check: CancelCheck = None,
) -> tuple[SessionActivity, ...]:
    check_cancel(cancel_check)
    method = getattr(store, "list_activity", None)
    if callable(method):
        return cast(
            tuple[SessionActivity, ...],
            method(
                session_ids=session_ids,
                exclude_session_ids=exclude_session_ids,
                cancel_check=cancel_check,
            ),
        )
    selected = set(session_ids) if session_ids is not None else None
    excluded = set(exclude_session_ids)
    result = []
    for summary in store.list():
        check_cancel(cancel_check)
        if (
            selected is None or summary.session_id in selected
        ) and summary.session_id not in excluded:
            result.append(
                SessionActivity(summary.session_id, summary.created_at, summary.updated_at)
            )
    return tuple(result)
