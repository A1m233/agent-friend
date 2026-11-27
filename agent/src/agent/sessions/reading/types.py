"""Small, owned results; ordinary readers never return an executable Session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from llm_providers import ModelRuntimeConfig

from ..session import Channel, Session
from ..store_types import SourceVersion


@dataclass(frozen=True)
class SessionState:
    session_id: str
    created_at: datetime
    initial_title: str
    initial_persona: str
    initial_model: str
    current_persona: str
    current_persona_id: str | None
    current_model_config: ModelRuntimeConfig
    current_channel: Channel
    origin_kind: str | None
    source_version: SourceVersion

    @property
    def current_model(self) -> str:
        return self.current_model_config.model_id

    @classmethod
    def from_session(cls, session: Session, version: SourceVersion) -> SessionState:
        return cls(
            session.session_id,
            session.created_at,
            session.initial_title,
            session.initial_persona,
            session.initial_model,
            session.current_persona,
            session.current_persona_id,
            session.current_model_config,
            session.current_channel,
            session.origin_kind,
            version,
        )


@dataclass(frozen=True)
class EventFilter:
    """Explicit selection, applied before bodies are deserialized. None means unconstrained."""

    kinds: tuple[str, ...] | None = None
    ids: tuple[str, ...] | None = None
    active: bool = True
    run_id: str | None = None
    evidence_ref: str | None = None
    event_tool_call_id: str | None = None
    tool_call_id: str | None = None
    attachments: bool = False
    exclude_tools: tuple[str, ...] = ()
    after: int = -1
    before: int | None = None
