"""Active conversation lineage projection for automatic memory recall."""

from __future__ import annotations

from ..sessions import SessionError, SessionStore


class SessionStoreSourceOrderProvider:
    """Expose only ordered active user/assistant event UUIDs to ``memory``."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def active_conversation_event_uuids(
        self,
        session_id: str,
    ) -> tuple[str, ...] | None:
        try:
            session = self._store.load(session_id)
        except SessionError:
            return None
        return tuple(
            event.uuid
            for event in session.active_events
            if _is_completed_conversation_event(event.type, event.payload)
        )


def _is_completed_conversation_event(event_type: str, payload: dict[str, object]) -> bool:
    if event_type not in ("user_message", "assistant_message"):
        return False
    if event_type == "assistant_message" and payload.get("partial"):
        return False
    content = payload.get("content")
    return isinstance(content, str) and bool(content.strip())
