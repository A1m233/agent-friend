"""Explicit reader capability for tests whose histories are already in memory."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace

from agent.sessions.query import CancelCheck, check_cancel
from agent.sessions.recall import RecallBuilder, SessionRecallSnapshot
from agent.sessions.session import Session


class RecallReaderFixture:
    def __init__(self, source: Callable[[str], Session]) -> None:
        self.source = source

    def recall_text(
        self, session_id: str, *, cancel_check: CancelCheck = None, boundary_id: str | None = None
    ) -> SessionRecallSnapshot:
        check_cancel(cancel_check)
        session = self.source(session_id)
        builder = RecallBuilder()
        for position, event in enumerate(session.events):
            check_cancel(cancel_check)
            builder.add(position, event)
        return builder.snapshot(
            count=len(session.events), version=None, diagnostics=session.load_diagnostics
        )

    @contextmanager
    def snapshot(
        self, session_id: str, *, cancel_check: CancelCheck = None
    ) -> Iterator[SimpleNamespace]:
        snapshot = self.recall_text(session_id, cancel_check=cancel_check)
        yield SimpleNamespace(conversation_ids=lambda: tuple(e.uuid for e in snapshot.events))
