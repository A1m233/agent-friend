"""Active conversation lineage projection for automatic memory recall."""

from __future__ import annotations

from memory.search.execution import CancelCheck, check_cancel

from ..sessions import SessionError, SessionStore
from ..sessions.reading import session_reader


class SessionStoreSourceOrderProvider:
    """Expose only ordered active user/assistant event UUIDs to ``memory``."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def active_conversation_event_uuids(
        self,
        session_id: str,
    ) -> tuple[str, ...] | None:
        return self.active_conversation_event_uuids_cancellable(session_id)

    def active_conversation_event_uuids_cancellable(
        self, session_id: str, *, cancel_check: CancelCheck | None = None
    ) -> tuple[str, ...] | None:
        check_cancel(cancel_check)
        try:
            with session_reader(self._store).snapshot(
                session_id, cancel_check=cancel_check
            ) as read:
                return read.conversation_ids()
        except SessionError:
            check_cancel(cancel_check)
            return None
