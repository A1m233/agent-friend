"""Small, source-bound composer state; normal writes advance it incrementally."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from ..errors import SessionCorruptError
from ..events import Event
from ..session import Session
from ..store_types import SourceVersion

_KINDS = {"session_meta", "persona_change", "model_change", "model_config_change", "channel_change"}


def write_cache(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class ConfigurationCache:
    def __init__(self, root: Path) -> None:
        self.root = root / ".configuration"

    def path(self, session_id: str) -> Path:
        return self.root / (sha256(session_id.encode()).hexdigest() + ".json")

    def read(self, session_id: str, version: SourceVersion) -> Session | None:
        try:
            value = json.loads(self.path(session_id).read_text(encoding="utf-8"))
            if value["schema"] != 1 or value["source"] != list(version):
                return None
            session = Session.from_events([Event.from_jsonl(line) for line in value["events"]])
            return session if session.session_id == session_id else None
        except (OSError, ValueError, KeyError, TypeError, SessionCorruptError):
            return None

    def publish(self, session: Session, version: SourceVersion) -> None:
        config = session.current_model_config
        head = session.events[0]
        normalized = replace(
            head,
            payload={
                **head.payload,
                "initial_model": config.model_id,
                "initial_reasoning_preset": config.reasoning_preset,
                "initial_persona": session.current_persona,
                "initial_persona_id": session.current_persona_id,
                "initial_channel": session.current_channel,
            },
        )
        write_cache(
            self.path(session.session_id),
            {"schema": 1, "source": version, "events": [normalized.to_jsonl()]},
        )

    def advance(
        self, session_id: str, before: SourceVersion, after: SourceVersion, events: list[Event]
    ) -> None:
        if any(event.type == "turn_rewrite" for event in events):
            return
        session = self.read(session_id, before)
        if session is not None:
            session.events.extend(event for event in events if event.type in _KINDS)
            self.publish(session, after)

    def delete(self, session_id: str) -> None:
        self.path(session_id).unlink(missing_ok=True)
