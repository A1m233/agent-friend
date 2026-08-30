"""Process-local identity for one Agent runtime instance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class RuntimeStartReason(StrEnum):
    DESKTOP_START = "desktop_start"
    CONTROLLED_RESTART = "controlled_restart"
    DEFAULT_MODEL_CHANGED = "default_model_changed"
    MODEL_PROVIDER_CHANGED = "model_provider_changed"
    REQUEST_SETTINGS_CHANGED = "request_settings_changed"
    SERVICE_RECOVERY = "service_recovery"
    HOST_RESUME_UNKNOWN = "host_resume_unknown"
    DIRECT_START = "direct_start"

    @classmethod
    def parse(cls, value: str | None) -> RuntimeStartReason:
        if value:
            try:
                return cls(value.strip())
            except ValueError:
                pass
        return cls.DIRECT_START


@dataclass(frozen=True)
class RuntimeInstanceIdentity:
    """Opaque boot identity shared by Bridge-owned runtime components."""

    bridge_instance_id: str = field(default_factory=lambda: uuid4().hex)
    supervisor_generation: int | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    start_reason: RuntimeStartReason = RuntimeStartReason.DIRECT_START

    @classmethod
    def create(
        cls,
        *,
        supervisor_generation: int | None = None,
        start_reason: RuntimeStartReason | str | None = None,
    ) -> RuntimeInstanceIdentity:
        return cls(
            supervisor_generation=supervisor_generation,
            start_reason=(
                start_reason
                if isinstance(start_reason, RuntimeStartReason)
                else RuntimeStartReason.parse(start_reason)
            ),
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "bridgeInstanceId": self.bridge_instance_id,
            "supervisorGeneration": self.supervisor_generation,
            "startedAt": self.started_at.isoformat(),
            "startReason": self.start_reason.value,
        }


__all__ = ["RuntimeInstanceIdentity", "RuntimeStartReason"]
