"""Host-authored notification facts; no model or Agent event dependency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .identity import validate_notification_id

HostEpisodeWriteResult = Literal["inserted", "duplicate", "deleted"]


class HostEpisodeConflict(ValueError):
    """An existing stable identity has different immutable fact data."""


@dataclass(frozen=True)
class HostEpisode:
    """A frozen, confirmed presentation receipt projected by trusted host code.

    ``summary`` is a deterministic description of what was actually presented,
    not model-extracted knowledge or an assertion that the user read it.
    """

    notification_id: str
    persona_id: str
    occurred_at: datetime
    summary: str
    owner_user_id: str = "local"

    def __post_init__(self) -> None:
        validate_notification_id(self.notification_id)
        if not self.owner_user_id.strip() or not self.persona_id.strip():
            raise ValueError("host episode owner and persona must be nonempty")
        if not self.summary.strip() or "\x00" in self.summary:
            raise ValueError("host episode summary must be nonempty and contain no NUL")
        if self.occurred_at.utcoffset() is None:
            raise ValueError("host episode occurred_at must include a timezone")
