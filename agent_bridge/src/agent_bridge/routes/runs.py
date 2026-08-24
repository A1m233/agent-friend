"""Desktop run lifecycle query and precise cancellation routes."""

from __future__ import annotations

import dataclasses
from typing import Any

from agent.runtime import AgentRuntime, RunCancelResult, RunSnapshot
from fastapi import APIRouter, FastAPI, HTTPException, Query

from ..assembly import BridgeRuntime

DESKTOP_TEXT_CONTROL_SCOPE = "desktop_text"


def _snapshot_payload(snapshot: RunSnapshot) -> dict[str, Any]:
    return dataclasses.asdict(snapshot)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1", tags=["runs"])

    def _agent_runtime() -> AgentRuntime:
        if runtime.agent_runtime is None:
            raise HTTPException(
                status_code=503,
                detail={"error": "runtime_unavailable", "message": "运行服务暂不可用。"},
            )
        return runtime.agent_runtime

    @router.get("/runs")
    def list_runs(
        include_recent_terminal: bool = Query(True, alias="includeRecentTerminal"),
    ) -> dict[str, Any]:
        snapshots = _agent_runtime().list_run_snapshots(
            include_recent_terminal=include_recent_terminal
        )
        return {"runs": [_snapshot_payload(snapshot) for snapshot in snapshots]}

    @router.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        snapshot = _agent_runtime().get_run_snapshot(run_id)
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "run_not_found", "message": "运行记录不存在或已过期。"},
            )
        return _snapshot_payload(snapshot)

    @router.post("/sessions/{session_id}/runs/{run_id}/cancel")
    def cancel_run(session_id: str, run_id: str) -> dict[str, Any]:
        result, snapshot = _agent_runtime().cancel_run(
            run_id,
            expected_session_id=session_id,
            required_control_scope=DESKTOP_TEXT_CONTROL_SCOPE,
        )
        if result is RunCancelResult.NOT_FOUND:
            raise HTTPException(
                status_code=404,
                detail={"error": "run_not_found", "message": "运行记录不存在或已过期。"},
            )
        if result is RunCancelResult.SESSION_MISMATCH:
            raise HTTPException(
                status_code=409,
                detail={"error": "run_session_mismatch", "message": "运行不属于当前会话。"},
            )
        if result is RunCancelResult.SCOPE_MISMATCH:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "run_not_desktop_controllable",
                    "message": "该运行需要从原入口停止。",
                },
            )
        if snapshot is None:  # pragma: no cover - defensive invariant
            raise HTTPException(status_code=500, detail="run snapshot unavailable")
        if runtime.approval_broker is not None:
            runtime.approval_broker.cancel_run(session_id, run_id)
        if runtime.command_runtime is not None:
            runtime.command_runtime.terminate_run(session_id, run_id)
        return {"result": result, "run": _snapshot_payload(snapshot)}

    app.include_router(router)
