"""Common approval request fields shared by typed capability payloads."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

ApprovalStatus = Literal["pending", "approved", "denied", "expired", "cancelled"]
ApprovalLifetime = Literal["once", "turn", "session", "persistent"]
ENABLED_APPROVAL_LIFETIMES: frozenset[ApprovalLifetime] = frozenset({"once"})


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

    def to_dict(self) -> dict[str, object]: ...

    def cancelled_decision(self) -> ApprovalDecisionLike: ...


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
