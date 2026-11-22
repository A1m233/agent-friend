from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent.approvals import ApprovalBroker
from agent.memory_maintenance.approval import PendingMemoryApproval
from agent_bridge.protocols.im.approvals.rendering import approval_text
from agent_bridge.routes.approvals import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def request() -> PendingMemoryApproval:
    return PendingMemoryApproval(
        request_id="memory-approval",
        kind="memory_maintenance",
        session_id="session",
        run_id="run",
        tool_call_id="call",
        details={
            "batch_id": "batch",
            "digest": "digest",
            "audit_id": "audit",
            "operations": [
                {
                    "action": "retire",
                    "old_disposition": "retire",
                    "reason": "一次性记录",
                    "targets": [
                        {"semantic_id": "fact", "revision": "revision", "statement": "原记录"}
                    ],
                    "replacement": None,
                    "source_refs": [],
                }
            ],
        },
    )


def test_memory_approval_route_rejects_wrong_run_and_decides_once() -> None:
    broker = ApprovalBroker()
    pending = request()
    ready = threading.Event()
    broker.add_listener(lambda value: ready.set() if value["status"] == "pending" else None)
    worker = threading.Thread(target=lambda: broker.request(pending, timeout_seconds=5))
    worker.start()
    try:
        assert ready.wait(2)
        app = FastAPI()
        register_routes(app, cast(Any, SimpleNamespace(approval_broker=broker)))
        with TestClient(app) as client:
            url = "/v1/approvals/memory-approval/memory-decision"
            body = {"session_id": "session", "run_id": "wrong", "allow": True}
            assert client.post(url, json=body).status_code == 409
            body["run_id"] = "run"
            response = client.post(url, json=body)
            assert response.status_code == 200
            assert response.json()["approval"]["details"] == pending.details
            assert client.post(url, json=body).status_code == 409
    finally:
        broker.cancel_run("session", "run")
        worker.join(5)


def test_im_memory_approval_is_lossless_or_falls_back_to_desktop() -> None:
    pending = request()
    text = approval_text(pending.to_dict(), "当前会话")
    assert "原记录" in text and "结束有效期" in text
    pending.details["operations"][0]["targets"][0]["statement"] = "长正文" * 1000
    with pytest.raises(ValueError, match="presentation budget"):
        approval_text(pending.to_dict(), "当前会话")
