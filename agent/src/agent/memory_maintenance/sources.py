"""Bounded direct conversation evidence. Episode IDs are never expanded."""

from __future__ import annotations

from typing import Any

from memory.maintenance.models import MaintenanceError
from memory.maintenance.service import SemanticMaintenance

from ..message_references import parse_quoted_text_references, render_memory_user_text
from ..sessions.errors import SessionError
from ..sessions.reading import EventFilter, session_reader
from ..sessions.store import SessionStore
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
        first, separator, last = event_range.partition("..")
        last = last if separator else first
        selected: list[dict[str, Any]] = []
        chars = 0
        control = control or ToolInvocationControl()
        try:
            with session_reader(self.sessions).snapshot(
                session_id, cancel_check=control.check_cancelled
            ) as read:
                bounds = read.source_range(first, last)
                if bounds is None or read.diagnostics.parse_error_count:
                    return {**base, "status": "unavailable", "reason": "source_incomplete"}
                start, end = bounds
                if read.inactive_in_range(start, end):
                    return {**base, "status": "unavailable", "reason": "source_rewritten"}
                if end - start >= 100000:
                    return {**base, "status": "unavailable", "reason": "source_scan_limit"}
                for _, event in read.scan(
                    EventFilter(
                        kinds=("user_message", "assistant_message"),
                        after=start - 1,
                        before=end + 1,
                        active=False,
                    ),
                    purpose="linked memory source range",
                ):
                    content = event.payload.get("content", "")
                    content = content if isinstance(content, str) else ""
                    if event.type == "user_message":
                        content = render_memory_user_text(
                            content,
                            parse_quoted_text_references(event.payload.get("references", [])),
                        )
                    text_end = chars + len(content)
                    if text_end > offset and chars < offset + limit:
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
                    chars = text_end
        except NotImplementedError:
            return {**base, "status": "unavailable", "reason": "source_reader_unavailable"}
        except (SessionError, OSError, ValueError):
            return {**base, "status": "unavailable", "reason": "source_missing_or_corrupt"}
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
