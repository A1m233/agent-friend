"""Active conversation lineage projection for automatic memory recall."""

from __future__ import annotations

from memory.search.execution import CancelCheck, check_cancel

from ..sessions import SessionError, SessionStore, query_session


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
            session = query_session(self._store, session_id, cancel_check=cancel_check)
        except SessionError:
            check_cancel(cancel_check)
            return None
        result: list[str] = []
        for event in session.active_events:
            check_cancel(cancel_check)
            if _is_completed_conversation_event(event.type, event.payload):
                result.append(event.uuid)
        return tuple(result)


def _is_completed_conversation_event(event_type: str, payload: dict[str, object]) -> bool:
    if event_type not in ("user_message", "assistant_message"):
        return False
    if event_type == "assistant_message" and payload.get("partial"):
        return False
    content = payload.get("content")
    return isinstance(content, str) and bool(content.strip())
