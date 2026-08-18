from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any, cast

from agent.approvals import ApprovalBroker
from agent.command_runtime import (
    CommandApprovalDetails,
    PendingCommandApproval,
)
from agent_bridge.routes.approvals import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_command_approval_is_listed_and_decided_on_common_control_plane() -> None:
    broker = ApprovalBroker()
    request = PendingCommandApproval.create(
        session_id="session-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="run deterministic check",
        details=CommandApprovalDetails(
            cmd="echo ok",
            argv=("shell", "-c", "echo ok"),
            workdir="C:/workspace",
            cwd_source="explicit",
            mode="buffered",
            timeout_seconds=30,
            filesystem_access="read_only",
            additional_roots=(),
            host_derived_roots=(),
            network_access="disabled",
            interactive=False,
        ),
    )
    decisions = []
    worker = threading.Thread(
        target=lambda: decisions.append(broker.request(request, timeout_seconds=2))
    )
    worker.start()
    deadline = time.monotonic() + 1
    while not broker.pending(kind="command") and time.monotonic() < deadline:
        time.sleep(0.01)

    app = FastAPI()
    register_routes(
        app,
        cast(Any, SimpleNamespace(filesystem_approval_broker=broker, command_runtime=None)),
    )
    client = TestClient(app)
    pending = client.get("/v1/approvals", params={"kind": "command"})
    assert pending.status_code == 200
    assert pending.json()["requests"][0]["details"]["cmd"] == "echo ok"

    unsupported = client.post(
        f"/v1/approvals/{request.request_id}/command-decision",
        json={
            "session_id": "session-1",
            "run_id": "run-1",
            "allow": True,
            "lifetime": "session",
        },
    )
    assert unsupported.status_code == 422
    assert broker.pending(kind="command")

    decided = client.post(
        f"/v1/approvals/{request.request_id}/command-decision",
        json={
            "session_id": "session-1",
            "run_id": "run-1",
            "allow": True,
            "lifetime": "once",
        },
    )
    assert decided.status_code == 200
    worker.join(timeout=2)
    assert decisions[0].approved is True


def test_windows_sandbox_setup_requires_explicit_endpoint_call() -> None:
    class FakeRuntime:
        setup_calls = 0

        def status(self) -> dict[str, object]:
            ready = self.setup_calls > 0
            return {
                "available": True,
                "platform": "windows",
                "sandbox_capability": {
                    "state": "ready" if ready else "setup_required",
                    "supported": True,
                    "ready": ready,
                    "last_error": None,
                    "version": self.setup_calls + 1,
                },
                "windows_sandbox_ready": ready,
                "requires_windows_sandbox_setup": not ready,
            }

        def setup_windows_sandbox(self) -> dict[str, object]:
            self.setup_calls += 1
            return self.status()

    command_runtime = FakeRuntime()
    app = FastAPI()
    register_routes(
        app,
        cast(
            Any,
            SimpleNamespace(
                filesystem_approval_broker=ApprovalBroker(),
                command_runtime=command_runtime,
            ),
        ),
    )
    client = TestClient(app)

    status = client.get("/v1/command-runtime/status")
    assert status.json()["requires_windows_sandbox_setup"] is True
    assert command_runtime.setup_calls == 0

    setup = client.post("/v1/command-runtime/windows-sandbox/setup")
    assert setup.status_code == 200
    assert setup.json()["windows_sandbox_ready"] is True
    assert command_runtime.setup_calls == 1


def test_command_allow_is_rejected_while_global_sandbox_is_not_ready() -> None:
    broker = ApprovalBroker()
    request = PendingCommandApproval.create(
        session_id="session-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="run check",
        details=CommandApprovalDetails(
            cmd="echo ok",
            argv=("cmd", "/c", "echo ok"),
            workdir="C:/workspace",
            cwd_source="explicit",
            mode="buffered",
            timeout_seconds=30,
            filesystem_access="read_only",
            additional_roots=(),
            host_derived_roots=(),
            network_access="disabled",
            interactive=False,
        ),
    )
    worker = threading.Thread(target=lambda: broker.request(request, timeout_seconds=2))
    worker.start()
    deadline = time.monotonic() + 1
    while not broker.pending(kind="command") and time.monotonic() < deadline:
        time.sleep(0.01)

    command_runtime = SimpleNamespace(
        status=lambda: {
            "platform": "windows",
            "sandbox_capability": {
                "state": "setup_required",
                "supported": True,
                "ready": False,
                "version": 1,
            },
        }
    )
    app = FastAPI()
    register_routes(
        app,
        cast(
            Any,
            SimpleNamespace(
                filesystem_approval_broker=broker,
                command_runtime=command_runtime,
            ),
        ),
    )
    response = TestClient(app).post(
        f"/v1/approvals/{request.request_id}/command-decision",
        json={
            "session_id": "session-1",
            "run_id": "run-1",
            "allow": True,
            "lifetime": "once",
        },
    )
    assert response.status_code == 409
    assert broker.pending(kind="command")
    broker.cancel_run("session-1", "run-1")
    worker.join(timeout=2)


def test_host_user_command_approval_does_not_require_windows_sandbox() -> None:
    broker = ApprovalBroker()
    request = PendingCommandApproval.create(
        session_id="session-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="launch desktop app",
        details=CommandApprovalDetails(
            cmd='"C:\\Program Files\\App\\app.exe"',
            argv=("C:\\Program Files\\App\\app.exe",),
            execution="host_user",
            workdir="C:/workspace",
            cwd_source="explicit",
            mode="buffered",
            timeout_seconds=30,
            filesystem_access="unrestricted",
            additional_roots=(),
            host_derived_roots=(),
            network_access="unrestricted",
            interactive=False,
        ),
    )
    worker = threading.Thread(target=lambda: broker.request(request, timeout_seconds=2))
    worker.start()
    deadline = time.monotonic() + 1
    while not broker.pending(kind="command") and time.monotonic() < deadline:
        time.sleep(0.01)
    command_runtime = SimpleNamespace(
        status=lambda: {
            "platform": "windows",
            "sandbox_capability": {"state": "setup_required", "ready": False},
        }
    )
    app = FastAPI()
    register_routes(
        app,
        cast(
            Any,
            SimpleNamespace(
                filesystem_approval_broker=broker,
                command_runtime=command_runtime,
            ),
        ),
    )

    response = TestClient(app).post(
        f"/v1/approvals/{request.request_id}/command-decision",
        json={
            "session_id": "session-1",
            "run_id": "run-1",
            "allow": True,
            "lifetime": "once",
        },
    )

    assert response.status_code == 200
    worker.join(timeout=2)
