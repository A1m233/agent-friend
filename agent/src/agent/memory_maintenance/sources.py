"""Bounded direct conversation evidence. Episode IDs are never expanded."""

from __future__ import annotations

from typing import Any

from memory.maintenance.models import MaintenanceError
from memory.maintenance.service import SemanticMaintenance

from ..message_references import parse_quoted_text_references, render_memory_user_text
from ..sessions.errors import SessionError
from ..sessions.store import SessionStore
from ..sessions.store_types import SessionReadState
from ..tool_runtime.control import ToolInvocationControl


class MaintenanceSources:
    def __init__(self, memory: SemanticMaintenance, sessions: SessionStore) -> None:
        self.memory, self.sessions = memory, sessions

    def read(
        self,
        semantic_id: str,
        source_ref: str,
        *,
        offset: int = 0,
        limit: int = 4000,
        control: ToolInvocationControl | None = None,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 8000:
            raise MaintenanceError("invalid_content_page")
        detail = self.memory.repository.detail(self.memory.owner, semantic_id)
        if source_ref not in detail["source_refs"]:
            raise MaintenanceError("source_not_linked_to_fact")
        base = {"semantic_id": semantic_id, "source_ref": source_ref}
        session_id, sep, event_range = source_ref.partition("#")
        if not sep or not session_id or not event_range:
            return {
                **base,
                "status": "unavailable",
                "reason": "not_a_direct_conversation_reference",
            }
        reader = getattr(self.sessions, "iter_events", None)
        if not callable(reader):
            return {**base, "status": "unavailable", "reason": "source_reader_unavailable"}
        first, separator, last = event_range.partition("..")
        last = last if separator else first
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        inactive: set[str] = set()
        started = ended = False
        consumed = 0
        chars = 0
        state = SessionReadState()
        control = control or ToolInvocationControl()
        try:
            events = reader(
                session_id, state, materialize_rollout=False, cancel_check=control.check_cancelled
            )
            try:
                for event in events:
                    control.check_cancelled()
                    consumed += 1
                    if consumed > 100000:
                        return {**base, "status": "unavailable", "reason": "source_scan_limit"}
                    if event.type == "turn_rewrite":
                        inactive.update(event.payload.get("inactive_event_uuids", []))
                    if event.uuid == first:
                        started = True
                    if started and not ended:
                        selected_ids.add(event.uuid)
                        if event.type in {"user_message", "assistant_message"}:
                            content = event.payload.get("content", "")
                            content = content if isinstance(content, str) else ""
                            if event.type == "user_message":
                                content = render_memory_user_text(
                                    content,
                                    parse_quoted_text_references(
                                        event.payload.get("references", [])
                                    ),
                                )
                            end = chars + len(content)
                            if end > offset and chars < offset + limit:
                                selected.append(
                                    {
                                        "event_id": event.uuid,
                                        "speaker": event.type.removesuffix("_message"),
                                        "timestamp": event.ts.isoformat(),
                                        "request_source": event.payload.get("request_source"),
                                        "partial": bool(event.payload.get("partial")),
                                        "content": content[
                                            max(0, offset - chars) : max(0, offset + limit - chars)
                                        ],
                                    }
                                )
                            chars = end
                    if started and event.uuid == last:
                        ended = True
            finally:
                close = getattr(events, "close", None)
                if callable(close):
                    close()
        except NotImplementedError:
            return {**base, "status": "unavailable", "reason": "source_reader_unavailable"}
        except (SessionError, OSError, ValueError):
            return {**base, "status": "unavailable", "reason": "source_missing_or_corrupt"}
        if not started or not ended or state.diagnostics.parse_error_count:
            return {**base, "status": "unavailable", "reason": "source_incomplete"}
        if selected_ids & inactive:
            return {**base, "status": "unavailable", "reason": "source_rewritten"}
        if not chars:
            return {**base, "status": "unavailable", "reason": "source_has_no_message_text"}
        return {
            **base,
            "status": "available",
            "messages": selected,
            "content_offset": offset,
            "content_length": chars,
            "next_content_offset": offset + limit if offset + limit < chars else None,
        }
