"""Generic approval and command-process control endpoints."""

from __future__ import annotations

import logging
from typing import Literal

from agent.approvals import (
    ApprovalModeConflictError,
    ApprovalRequestStaleError,
)
from agent.command_runtime import CommandApprovalDecision, CommandRuntimeError
from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from agent import SessionNotFoundError

from ..assembly import BridgeRuntime

logger = logging.getLogger(__name__)


class CommandApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    allow: bool
    lifetime: Literal["once", "turn", "session", "persistent"] = "once"


class SessionApprovalModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["per_request", "session_auto"]
    expected_version: int = Field(ge=0)
    risk_acknowledged: bool = False


class EnableSessionAutoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    expected_request_version: int = Field(ge=1)
    expected_mode_version: int = Field(ge=0)
    risk_acknowledged: bool


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1", tags=["approvals"])

    @router.get("/approvals")
    def pending_approvals(
        session_id: str | None = Query(default=None, min_length=1),
        run_id: str | None = Query(default=None, min_length=1),
        kind: Literal["filesystem", "command"] | None = Query(default=None),
    ) -> dict[str, object]:
        broker = runtime.approval_broker
        if broker is None:
            raise HTTPException(status_code=503, detail="approval unavailable")
        return {
            "requests": [
                request.to_dict()
                for request in broker.pending(session_id=session_id, run_id=run_id, kind=kind)
            ]
        }

    @router.put("/sessions/{session_id}/approval-mode")
    def set_session_approval_mode(
        session_id: str,
        body: SessionApprovalModeBody,
    ) -> dict[str, object]:
        broker = runtime.approval_broker
        if broker is None:
            raise HTTPException(status_code=503, detail="approval unavailable")
        if body.mode == "session_auto" and not body.risk_acknowledged:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "approval_risk_acknowledgement_required",
                    "message": "进入本会话无需授权前必须重新确认风险。",
                },
            )
        try:
            state = broker.set_session_mode(
                session_id,
                mode=body.mode,
                expected_version=body.expected_version,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        except ApprovalModeConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "approval_mode_conflict",
                    "message": "会话授权模式已经变化，请刷新后重新选择。",
                    **exc.current.to_dict(),
                },
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="approval mode unavailable") from exc
        return {"session_id": session_id, **state.to_dict()}

    @router.get("/sessions/{session_id}/approval-mode")
    def get_session_approval_mode(session_id: str) -> dict[str, object]:
        broker = runtime.approval_broker
        if broker is None:
            raise HTTPException(status_code=503, detail="approval unavailable")
        try:
            manager = getattr(runtime, "persistent_session_manager", None)
            if manager is not None and not manager.exists(session_id):
                raise SessionNotFoundError(f"会话不存在: {session_id}")
            state = broker.mode_state(session_id)
        except (KeyError, SessionNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return {"session_id": session_id, **state.to_dict()}

    @router.post("/approvals/{request_id}/enable-session-auto")
    def approve_and_enable_session_auto(
        request_id: str,
        body: EnableSessionAutoBody,
    ) -> dict[str, object]:
        broker = runtime.approval_broker
        if broker is None:
            raise HTTPException(status_code=503, detail="approval unavailable")
        if not body.risk_acknowledged:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "approval_risk_acknowledgement_required",
                    "message": "进入本会话无需授权前必须重新确认风险。",
                },
            )

        def ensure_ready(request: object) -> None:
            if getattr(request, "kind", None) == "command":
                _ensure_command_request_ready(runtime, request)

        try:
            state = broker.decide_and_enable_session_auto(
                request_id,
                session_id=body.session_id,
                run_id=body.run_id,
                expected_request_version=body.expected_request_version,
                expected_mode_version=body.expected_mode_version,
                precondition=ensure_ready,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        except ApprovalRequestStaleError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "approval_request_stale",
                    "message": "授权请求已经变化或不再等待处理。",
                },
            ) from exc
        except ApprovalModeConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "approval_mode_conflict",
                    "message": "会话授权模式已经变化，请刷新后重新选择。",
                    **exc.current.to_dict(),
                },
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="approval mode unavailable") from exc
        return {"accepted": True, "session_id": body.session_id, **state.to_dict()}

    @router.post("/approvals/{request_id}/command-decision")
    def decide_command_approval(
        request_id: str, body: CommandApprovalDecisionBody
    ) -> dict[str, object]:
        broker = runtime.approval_broker
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
        stale_detail = {
            "error": "approval_request_stale",
            "message": "这项授权已处理或已失效，请查看当前任务状态。",
        }
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
            raise HTTPException(status_code=404, detail=stale_detail)
        if body.allow:
            _ensure_command_request_ready(runtime, pending_request)
        decision = CommandApprovalDecision(allow=body.allow, lifetime=body.lifetime)
        if not broker.decide(
            request_id,
            decision,
            session_id=body.session_id,
            run_id=body.run_id,
            expected_kind="command",
            lifetime=body.lifetime,
        ):
            raise HTTPException(status_code=404, detail=stale_detail)
        return {"accepted": True, "approval": pending_request.to_dict()}

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


def _ensure_command_request_ready(runtime: BridgeRuntime, request: object) -> None:
    details = getattr(request, "details", None)
    if getattr(details, "execution", None) != "sandboxed" or runtime.command_runtime is None:
        return
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
