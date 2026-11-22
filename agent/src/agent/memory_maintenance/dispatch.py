"""Runtime dispatch, independent of the model-visible tool definitions."""

from __future__ import annotations

from typing import Any

from memory.maintenance.models import MaintenanceError
from memory.maintenance.service import SemanticMaintenance

from ..approvals.broker import ApprovalBroker
from ..sessions.store import SessionStore
from ..tool_runtime.control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl
from .service import MaintenanceApplication, MaintenanceInvocation
from .sources import MaintenanceSources

INVOCATION_ARG = "__agent_friend_memory_maintenance_invocation"
TOOL_NAMES = frozenset(
    {
        "semantic_memory_query",
        "semantic_memory_detail",
        "semantic_memory_sources",
        "semantic_memory_audit",
        "semantic_memory_changes",
    }
)


class MaintenanceDispatch:
    def __init__(
        self,
        memory: SemanticMaintenance,
        sessions: SessionStore | None,
        broker: ApprovalBroker | None,
    ) -> None:
        self.memory = memory
        self.sources = MaintenanceSources(memory, sessions) if sessions else None
        self.application = MaintenanceApplication(memory, broker)

    def invoke(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = self._invoke(name, args)
        invocation = args.get(INVOCATION_ARG)
        if isinstance(invocation, MaintenanceInvocation) and invocation.user_source_ref:
            result["current_user_source_ref"] = invocation.user_source_ref
        return result

    def _invoke(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        repo, owner = self.memory.repository, self.memory.owner
        control = args.get(TOOL_INVOCATION_CONTROL_ARG)
        control = control if isinstance(control, ToolInvocationControl) else ToolInvocationControl()
        control.check_cancelled()
        if name == "semantic_memory_query":
            return repo.query(
                owner,
                query=args.get("query", ""),
                after_id=args.get("after_id", ""),
                limit=args.get("limit", 20),
            )
        if name == "semantic_memory_detail":
            return repo.detail(
                owner,
                args["semantic_id"],
                offset=args.get("offset", 0),
                limit=args.get("limit", 4000),
            )
        if name == "semantic_memory_sources":
            if self.sources is None:
                raise MaintenanceError("source_reader_unavailable")
            return self.sources.read(
                args["semantic_id"],
                args["source_ref"],
                offset=args.get("offset", 0),
                limit=args.get("limit", 4000),
                control=control,
            )
        invocation = args.get(INVOCATION_ARG)
        if not isinstance(invocation, MaintenanceInvocation):
            raise MaintenanceError("host_invocation_required")
        action = args["action"]
        if name == "semantic_memory_audit":
            if action == "list":
                return {"audits": repo.audits(owner, limit=args.get("limit", 20))}
            if action == "status":
                return repo.status(owner, args["audit_id"])
            if action == "page":
                return repo.audit_page(
                    owner,
                    args["audit_id"],
                    offset=args.get("offset", 0),
                    limit=args.get("limit", 20),
                    pending_only=args.get("pending_only", True),
                )
            if not invocation.user_source_ref:
                raise MaintenanceError("user_initiated_maintenance_required")
            if action == "start":
                return repo.start(
                    owner,
                    invocation.session_id,
                    query=args.get("query", ""),
                    semantic_ids=args.get("semantic_ids"),
                )
            if action == "refresh":
                return repo.refresh(owner, args["audit_id"], args["semantic_ids"])
            if action == "record":
                return repo.record(
                    owner,
                    args["audit_id"],
                    args["decisions"],
                    user_source_ref=invocation.user_source_ref,
                )
        if name == "semantic_memory_changes":
            if action == "list":
                return repo.batches(
                    owner,
                    args["audit_id"],
                    after_id=args.get("after_id", ""),
                    limit=args.get("limit", 20),
                )
            if action == "status":
                return repo.batch(owner, args["batch_id"])
            if action == "prepare":
                if not invocation.user_source_ref:
                    raise MaintenanceError("user_initiated_maintenance_required")
                return repo.prepare(
                    owner,
                    invocation.session_id,
                    args["audit_id"],
                    args["semantic_ids"],
                    user_source_ref=invocation.user_source_ref,
                )
            if action == "apply":
                return self.application.apply(args["batch_id"], invocation, control)
        raise MaintenanceError("unknown_maintenance_action")
