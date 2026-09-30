"""Build a compact execution Session while validating the entire durable journal."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from .events import ROLLOUT_EVENT_TYPES, Event
from .query import CancelCheck, check_cancel
from .rollout import RolloutRestoreResult, restore_rollout
from .session import Session
from .store_types import SessionReadState


@contextmanager
def close_iterator(source: Iterator[Event]) -> Iterator[Iterator[Event]]:
    try:
        yield source
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()


def load_execution_session(
    reader: Callable[[SessionReadState], Iterator[Event]],
    *,
    cancel_check: CancelCheck = None,
) -> Session:
    state = SessionReadState()
    with close_iterator(reader(state)) as source:
        head = next(source, None)
        session = Session.from_events([head] if head is not None else [])

        def events() -> Iterator[Event]:
            yield session.events[0]
            for event in source:
                check_cancel(cancel_check)
                if event.type not in ROLLOUT_EVENT_TYPES:
                    session.append(event)
                yield event
            session.load_diagnostics = state.diagnostics

        with close_iterator(events()) as stream:
            restored = restore_rollout(session, events=stream, cancel_check=cancel_check)
            # A rejected commit returns a degraded state early. The complete file
            # still needs parsing/corruption validation and business projection.
            for _ in stream:
                pass
    session.loaded_source_version = state.source_version
    session.restored_rollout = restored
    return session


def restore_prefix(
    session: Session, commit_uuid: str, *, cancel_check: CancelCheck = None
) -> RolloutRestoreResult:
    """Replay one historical prefix, exhausting the reader to validate its source."""
    assert session.journal_reader is not None
    prefix = Session.from_events([session.events[0]])
    with close_iterator(session.journal_reader()) as source:

        def events() -> Iterator[Event]:
            reached = False
            for event in source:
                check_cancel(cancel_check)
                if reached:
                    continue
                if event.type not in ROLLOUT_EVENT_TYPES and event.uuid != prefix.session_id:
                    prefix.append(event)
                yield event
                reached = event.uuid == commit_uuid

        with close_iterator(events()) as stream:
            result = restore_rollout(prefix, events=stream, cancel_check=cancel_check)
            for _ in stream:
                pass
    return result
