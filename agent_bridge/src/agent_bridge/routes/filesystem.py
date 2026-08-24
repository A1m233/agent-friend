"""Desktop REST endpoints for one-shot filesystem approval decisions."""

from __future__ import annotations

from typing import Literal

from agent.filesystem import ApprovalDecision
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..assembly import BridgeRuntime

PermissionName = Literal["search_metadata", "read_text", "create", "edit"]


class FilesystemApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    approved_permissions: dict[str, list[PermissionName]] = Field(default_factory=dict)
    approved_action_ids: list[str] = Field(default_factory=list)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    @app.get("/v1/filesystem/approvals")
    def pending_filesystem_approvals(
        session_id: str | None = Query(default=None, min_length=1),
    ) -> dict[str, object]:
        broker = runtime.approval_broker
        if broker is None:
            raise HTTPException(status_code=503, detail="filesystem approval unavailable")
        return {
            "requests": [
                request.to_dict()
                for request in broker.pending(session_id=session_id, kind="filesystem")
            ]
        }

    @app.post("/v1/filesystem/approvals/{request_id}/decision")
    def decide_filesystem_approval(
        request_id: str,
        body: FilesystemApprovalDecisionBody,
    ) -> dict[str, object]:
        broker = runtime.approval_broker
        if broker is None:
            raise HTTPException(status_code=503, detail="filesystem approval unavailable")
        decision = ApprovalDecision(
            approved_permissions={
                key: frozenset(value) for key, value in body.approved_permissions.items()
            },
            approved_action_ids=frozenset(body.approved_action_ids),
        )
        if not broker.decide(
            request_id,
            decision,
            session_id=body.session_id,
            run_id=body.run_id,
            expected_kind="filesystem",
        ):
            raise HTTPException(status_code=404, detail="approval request not pending")
        return {"accepted": True}
