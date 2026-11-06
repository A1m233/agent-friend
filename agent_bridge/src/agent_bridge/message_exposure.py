"""Validate desktop observations and project version-bound evidence for the Agent."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from agent.message_exposure import MessageExposureStore, content_version
from agent.paths import message_exposure_db_path
from agent.sessions.events import Event

if TYPE_CHECKING:
    from .assembly import BridgeRuntime


class MessageExposureService:
    def __init__(self, runtime: BridgeRuntime) -> None:
        self.runtime = runtime
        self.store = MessageExposureStore(
            message_exposure_db_path(runtime.settings.sessions_dir.parent)
        )

    def record(self, session_id: str, observations: list[Any], *, now: datetime) -> None:
        manager = self.runtime.persistent_session_manager
        if not manager.exists(session_id):
            return
        events = {e.uuid: e for e in manager.query(session_id).active_events}
        for item in observations:
            event = events.get(item.message_id)
            if (
                event is None
                or event.type != "assistant_message"
                or event.payload.get("partial")
                or content_version(str(event.payload.get("content", ""))) != item.content_version
            ):
                continue
            self.store.record(
                session_id, event.uuid, item.content_version, item.extent, observed_at=now
            )

    def evidence(self, session_id: str, event: Event) -> dict[str, Any]:
        result: dict[str, Any] = {
            "desktop": self.store.get(
                session_id, event.uuid, content_version(str(event.payload.get("content", "")))
            ),
            "im_read_status": "unknown",
        }
        if self.runtime.session_delivery is not None:
            result["deliveries"] = self.runtime.session_delivery.store.for_message(
                session_id, event.uuid
            )
        return result

    def changes(self, after: int) -> dict[str, Any]:
        page = self.store.changes(after)
        valid: list[dict[str, Any]] = []
        manager = self.runtime.persistent_session_manager
        sessions: dict[str, dict[str, Event]] = {}
        for item in page["items"]:
            sid = item["session_id"]
            if not manager.exists(sid):
                self.store.delete_session(sid)
                continue
            if sid not in sessions:
                sessions[sid] = {e.uuid: e for e in manager.query(sid).active_events}
            event = sessions[sid].get(item["message_id"])
            if (
                event is None
                or content_version(str(event.payload.get("content", ""))) != item["content_version"]
            ):
                continue
            valid.append(
                {
                    "session_id": sid,
                    "message_id": event.uuid,
                    "desktop": self.evidence(sid, event)["desktop"],
                }
            )
        return {**page, "items": valid}
