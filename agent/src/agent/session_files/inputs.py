"""Per-conversation preparation boundary and side-effect-free input projection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..attachments import AttachmentError
from ..attachments.input_projection import project_file_message
from ..messages import Message
from .errors import FILE_ERROR_MESSAGES
from .service import SessionFileService


class SessionFileInputs:
    def __init__(self, service: SessionFileService, session_id: str) -> None:
        self.service = service
        self.session_id = session_id
        self._event_id: str | None = None
        self._prepared: list[dict[str, Any]] = []

    def prepare(self, event_id: str | None, cancel_check: Callable[[], None] | None = None) -> None:
        self._event_id = None
        self._prepared = []
        if event_id is None:
            return
        files = [
            item
            for item in self.service.files(self.session_id)
            if item.source_message_ref == event_id
        ]
        prepared = []
        for item in files:
            if cancel_check is not None:
                cancel_check()
            try:
                prepared.append(self.service.prepare(self.session_id, item.file_ref))
            except (ValueError, OSError) as exc:
                code = str(exc) if isinstance(exc, ValueError) else "session_file_io_error"
                if code not in FILE_ERROR_MESSAGES:
                    code = "session_file_io_error"
                raise AttachmentError(code, FILE_ERROR_MESSAGES[code]) from exc
        self._event_id, self._prepared = event_id, prepared

    def project(self, message: Message) -> Message:
        current = message.meta.get("current_input") or message.uuid == self._event_id
        matches = (
            current
            and len(message.attachments) == len(self._prepared)
            and all(
                ref.to_dict() == {key: item[key] for key in ref.to_dict()}
                for ref, item in zip(message.attachments, self._prepared, strict=True)
            )
        )
        return project_file_message(message, self._prepared if matches else None)
