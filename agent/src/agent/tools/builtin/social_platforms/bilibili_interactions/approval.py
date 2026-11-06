from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .....approvals.models import (
    ApprovalDecisionSource,
    ApprovalLifecycle,
    RiskAssessment,
    SessionApprovalMode,
)
from .....command_runtime.models import CommandApprovalDecision


@dataclass
class PendingSocialApproval(ApprovalLifecycle):
    reason: str = "公开回复 B 站评论"
    details: dict[str, Any] = field(default_factory=dict)
    approval_mode: SessionApprovalMode = "per_request"
    approval_mode_source: str = "session"
    approval_mode_version: int = 0
    decision_source: ApprovalDecisionSource | None = None
    risk_assessment: RiskAssessment | None = None
    review_assessment: dict[str, object] | None = None
    review_fallback: str | None = None
    review_fingerprint: str | None = None
    review_context: object | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, object]:
        return {
            **self.lifecycle_dict(),
            "reason": self.reason,
            "details": self.details,
            "approval_mode": self.approval_mode,
            "approval_mode_source": self.approval_mode_source,
            "approval_mode_version": self.approval_mode_version,
            "decision_source": self.decision_source,
            "review": self.review_assessment,
            "review_fallback": self.review_fallback,
            "review_fingerprint": self.review_fingerprint,
            "risk": self.risk_assessment.to_dict() if self.risk_assessment else None,
        }

    @staticmethod
    def cancelled_decision() -> CommandApprovalDecision:
        return CommandApprovalDecision(allow=False)

    @staticmethod
    def fully_approved_decision() -> CommandApprovalDecision:
        return CommandApprovalDecision(allow=True)
