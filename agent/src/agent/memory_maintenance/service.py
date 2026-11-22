from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from memory.maintenance.models import MaintenanceAuthorization, MaintenanceError
from memory.maintenance.service import SemanticMaintenance

from ..approvals.broker import ApprovalBroker
from ..tool_runtime.control import ToolInvocationControl
from .approval import PendingMemoryApproval


@dataclass(frozen=True)
class MaintenanceInvocation:
    """Constructed by the conversation host; never accepted from public tool arguments."""

    session_id: str
    run_id: str
    tool_call_id: str
    surface: str
    user_source_ref: str | None
    review_context: object | None = None


class MaintenanceApplication:
    def __init__(self, memory: SemanticMaintenance, broker: ApprovalBroker | None) -> None:
        self.memory = memory
        self.broker = broker

    def apply(
        self,
        batch_id: str,
        invocation: MaintenanceInvocation,
        control: ToolInvocationControl,
    ) -> dict[str, Any]:
        if not all((invocation.session_id, invocation.run_id, invocation.tool_call_id)):
            raise MaintenanceError("execution_identity_missing")
        batch = self.memory.repository.batch(self.memory.owner, batch_id)
        if batch["session_id"] != invocation.session_id:
            raise MaintenanceError("batch_session_mismatch")
        if batch["receipt"] is not None:
            return dict(batch["receipt"])
        if self.broker is None:
            raise MaintenanceError("approval_unavailable")
        if not invocation.user_source_ref:
            raise MaintenanceError("user_initiated_maintenance_required")
        request = PendingMemoryApproval(
            request_id=f"memoryreq_{uuid4().hex}",
            kind="memory_maintenance",
            session_id=invocation.session_id,
            run_id=invocation.run_id,
            tool_call_id=invocation.tool_call_id,
            details={"batch_id": batch_id, "digest": batch["digest"], **batch["payload"]},
            review_context=invocation.review_context,
        )
        broker = self.broker
        token = control.add_cancel_callback(
            lambda _: broker.cancel_run(invocation.session_id, invocation.run_id)
        )
        try:
            control.check_cancelled()
            decision = broker.request(request, timeout_seconds=300, surface=invocation.surface)
        finally:
            control.remove_cancel_callback(token)
        if decision is None or not decision.approved:
            return {
                "status": "not_committed",
                "error": f"approval_{request.status}",
                "batch_id": batch_id,
            }
        control.check_cancelled()
        if request.approval_mode in {"auto_review", "full_access"} and not broker.policy_current(
            request
        ):
            raise MaintenanceError("approval_policy_changed")
        return self.memory.commit(
            MaintenanceAuthorization(
                batch_id=batch_id,
                batch_digest=batch["digest"],
                request_id=request.request_id,
                session_id=invocation.session_id,
                decision_source=request.decision_source or "user_once",
                source_ref=invocation.user_source_ref,
            )
        )
