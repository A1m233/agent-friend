"""Generic approval and command-process control endpoints."""

from __future__ import annotations

import logging
from typing import Literal

from agent.command_runtime import CommandApprovalDecision, CommandRuntimeError
from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..assembly import BridgeRuntime

logger = logging.getLogger(__name__)


class CommandApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    allow: bool
    lifetime: Literal["once", "turn", "session", "persistent"] = "once"


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1", tags=["approvals"])

    @router.get("/approvals")
    def pending_approvals(
        session_id: str | None = Query(default=None, min_length=1),
        run_id: str | None = Query(default=None, min_length=1),
        kind: Literal["filesystem", "command"] | None = Query(default=None),
    ) -> dict[str, object]:
        broker = runtime.filesystem_approval_broker
        if broker is None:
            raise HTTPException(status_code=503, detail="approval unavailable")
        return {
            "requests": [
                request.to_dict()
                for request in broker.pending(session_id=session_id, run_id=run_id, kind=kind)
            ]
        }

    @router.post("/approvals/{request_id}/command-decision")
    def decide_command_approval(
        request_id: str, body: CommandApprovalDecisionBody
    ) -> dict[str, object]:
        broker = runtime.filesystem_approval_broker
        if broker is None:
            raise HTTPException(status_code=503, detail="approval unavailable")
        if body.lifetime != "once":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "approval_lifetime_not_enabled",
                    "message": "当前版本只支持仅本次授权。",
                },
            )
        pending_request = next(
            (
                item
                for item in broker.pending(
                    session_id=body.session_id,
                    run_id=body.run_id,
                    kind="command",
                )
                if item.request_id == request_id
            ),
            None,
        )
        if pending_request is None:
            raise HTTPException(status_code=404, detail="approval request not pending")
        if (
            body.allow
            and pending_request.details.execution == "sandboxed"
            and runtime.command_runtime is not None
        ):
            status = runtime.command_runtime.status()
            capability = status.get("sandbox_capability")
            if (
                status.get("platform") == "windows"
                and isinstance(capability, dict)
                and capability.get("state") != "ready"
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "windows_sandbox_setup_required",
                        "message": "请先在全局系统权限中初始化或修复命令沙箱。",
                        "sandbox_capability": capability,
                    },
                )
        decision = CommandApprovalDecision(allow=body.allow, lifetime=body.lifetime)
        if not broker.decide(
            request_id,
            decision,
            session_id=body.session_id,
            run_id=body.run_id,
            expected_kind="command",
            lifetime=body.lifetime,
        ):
            raise HTTPException(status_code=404, detail="approval request not pending")
        return {"accepted": True}

    @router.get("/command-processes")
    def command_processes(
        session_id: str | None = Query(default=None, min_length=1),
        include_terminal: bool = Query(default=True),
    ) -> dict[str, object]:
        if runtime.command_runtime is None:
            raise HTTPException(status_code=503, detail="command runtime unavailable")
        return {
            "processes": [
                item.to_dict()
                for item in runtime.command_runtime.snapshots(
                    session_id=session_id, include_terminal=include_terminal
                )
            ]
        }

    @router.get("/command-runtime/status")
    def command_runtime_status() -> dict[str, object]:
        if runtime.command_runtime is None:
            raise HTTPException(status_code=503, detail="command runtime unavailable")
        return runtime.command_runtime.status()

    @router.post("/command-runtime/windows-sandbox/setup")
    def setup_windows_command_sandbox() -> dict[str, object]:
        if runtime.command_runtime is None:
            raise HTTPException(status_code=503, detail="command runtime unavailable")
        try:
            return runtime.command_runtime.setup_windows_sandbox()
        except CommandRuntimeError as exc:
            logger.warning(
                "Windows command sandbox setup failed error_kind=%s message=%s",
                exc.code,
                str(exc),
            )
            raise HTTPException(
                status_code=409,
                detail={"error": exc.code, "message": str(exc)},
            ) from exc

    @router.post("/sessions/{session_id}/runs/{run_id}/commands/{process_id}/cancel")
    def cancel_command_process(session_id: str, run_id: str, process_id: str) -> dict[str, object]:
        if runtime.command_runtime is None:
            raise HTTPException(status_code=503, detail="command runtime unavailable")
        try:
            snapshot = runtime.command_runtime.terminate(
                process_id, session_id=session_id, run_id=run_id
            )
        except CommandRuntimeError as exc:
            raise HTTPException(
                status_code=404 if exc.code == "process_not_found" else 409,
                detail={"error": exc.code, "message": str(exc)},
            ) from exc
        return snapshot.to_dict()

    app.include_router(router)
