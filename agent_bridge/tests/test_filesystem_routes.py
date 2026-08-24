from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agent.filesystem import (
    ApprovalDecision,
    ApprovalItem,
    FilesystemApprovalBroker,
    PendingFilesystemApproval,
)
from agent_bridge.routes.filesystem import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_desktop_can_list_and_decide_pending_approval(tmp_path: Path) -> None:
    broker = FilesystemApprovalBroker()
    request = PendingFilesystemApproval.create(
        session_id="session-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="读取说明文件",
        items=(
            ApprovalItem(
                item_id="docs",
                path=tmp_path.resolve(),
                root_kind="directory",
                permissions=frozenset({"search_metadata", "read_text"}),
            ),
        ),
        actions=(),
    )
    decisions: list[ApprovalDecision | None] = []
    worker = threading.Thread(
        target=lambda: decisions.append(broker.request(request, timeout_seconds=2))
    )
    worker.start()
    deadline = time.monotonic() + 1
    while not broker.pending_for_session("session-1") and time.monotonic() < deadline:
        time.sleep(0.01)

    app = FastAPI()
    register_routes(app, cast(Any, SimpleNamespace(approval_broker=broker)))
    client = TestClient(app)
    pending = client.get("/v1/filesystem/approvals", params={"session_id": "session-1"})
    assert pending.status_code == 200
    assert pending.json()["requests"][0]["items"][0]["path"] == str(tmp_path.resolve())
    assert pending.json()["requests"][0]["tool_call_id"] == "tool-1"
    assert client.get("/v1/filesystem/approvals").json() == pending.json()

    mismatched = client.post(
        f"/v1/filesystem/approvals/{request.request_id}/decision",
        json={
            "session_id": "another-session",
            "run_id": "run-1",
            "approved_permissions": {},
            "approved_action_ids": [],
        },
    )
    assert mismatched.status_code == 404

    decided = client.post(
        f"/v1/filesystem/approvals/{request.request_id}/decision",
        json={
            "session_id": "session-1",
            "run_id": "run-1",
            "approved_permissions": {"docs": ["search_metadata"]},
            "approved_action_ids": [],
        },
    )
    assert decided.status_code == 200
    worker.join(timeout=2)
    assert decisions[0] is not None
    assert decisions[0].approved_permissions == {"docs": frozenset({"search_metadata"})}


def test_expired_approval_cannot_be_decided() -> None:
    broker = FilesystemApprovalBroker()
    app = FastAPI()
    register_routes(app, cast(Any, SimpleNamespace(approval_broker=broker)))

    response = TestClient(app).post(
        "/v1/filesystem/approvals/missing/decision",
        json={
            "session_id": "session-1",
            "run_id": "run-1",
            "approved_permissions": {},
            "approved_action_ids": [],
        },
    )
    assert response.status_code == 404
