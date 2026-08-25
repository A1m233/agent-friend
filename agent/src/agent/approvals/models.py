"""Common approval request fields shared by typed capability payloads."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

ApprovalStatus = Literal["pending", "approved", "denied", "expired", "cancelled"]
ApprovalLifetime = Literal["once", "turn", "session", "persistent"]
ENABLED_APPROVAL_LIFETIMES: frozenset[ApprovalLifetime] = frozenset({"once"})
SessionApprovalMode = Literal["per_request", "session_auto"]
ApprovalDecisionSource = Literal[
    "user_once",
    "user_enable_session_auto",
    "session_auto",
    "catastrophic_policy",
]
RiskLevel = Literal["normal", "explicit", "catastrophic"]


@dataclass(frozen=True)
class SessionApprovalModeState:
    """Effective per-session approval routing state.

    The mode controls how each new typed one-shot request is decided.  It is
    deliberately separate from ``ApprovalLifetime`` so a session setting can
    never turn one approved path or command into a reusable capability.
    """

    mode: SessionApprovalMode = "per_request"
    version: int = 0

    def to_dict(self) -> dict[str, object]:
        return {"approval_mode": self.mode, "approval_mode_version": self.version}


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    fingerprint: str
    code: str | None = None
    message: str | None = None
    evidence: tuple[str, ...] = ()

    @property
    def catastrophic(self) -> bool:
        return self.level == "catastrophic"

    @property
    def requires_explicit_user(self) -> bool:
        return self.level == "explicit"

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "fingerprint": self.fingerprint,
            "code": self.code,
            "message": self.message,
            "evidence": list(self.evidence),
        }


@runtime_checkable
class ApprovalDecisionLike(Protocol):
    @property
    def approved(self) -> bool: ...


@runtime_checkable
class PendingApproval(Protocol):
    request_id: str
    kind: str
    session_id: str
    run_id: str
    tool_call_id: str
    requested_lifetime: ApprovalLifetime
    status: ApprovalStatus
    version: int
    _decision: object | None
    _event: threading.Event
    approval_mode: SessionApprovalMode
    approval_mode_version: int
    decision_source: ApprovalDecisionSource | None
    risk_assessment: RiskAssessment | None

    def to_dict(self) -> dict[str, object]: ...

    def cancelled_decision(self) -> ApprovalDecisionLike: ...

    def fully_approved_decision(self) -> ApprovalDecisionLike: ...


@dataclass
class ApprovalLifecycle:
    """Reusable lifecycle fields for typed approval request dataclasses."""

    request_id: str
    kind: str
    session_id: str
    run_id: str
    tool_call_id: str
    requested_lifetime: ApprovalLifetime = "once"
    version: int = 1
    status: ApprovalStatus = "pending"
    _decision: object | None = field(default=None, repr=False)
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    def lifecycle_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "requested_lifetime": self.requested_lifetime,
            "version": self.version,
            "status": self.status,
        }
