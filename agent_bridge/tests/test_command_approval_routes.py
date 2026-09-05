from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent.approvals import ApprovalBroker
from agent.command_runtime import (
    CommandApprovalDetails,
    PendingCommandApproval,
)
from agent.sessions.errors import SessionCorruptError, SessionPersistError
from agent.sessions.metadata import SessionMetadataStore
from agent_bridge.routes.approvals import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import Event, JsonlSessionStore, Session, SessionManager, SessionNotFoundError


@pytest.fixture
def approval_sessions(
    tmp_path: Path,
) -> tuple[JsonlSessionStore, SessionMetadataStore, ApprovalBroker, TestClient]:
    store = JsonlSessionStore(tmp_path / "sessions")
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    manager = SessionManager(store, metadata_store=metadata)
    manager.create("default", "deepseek/deepseek-chat", session_id="session-1")

    def validate_session(session_id: str) -> bool:
        # Preserve the production broker's full-history precondition for writes.
        try:
            store.load(session_id)
            return True
        except SessionNotFoundError:
            return False

    broker = ApprovalBroker(mode_store=metadata, session_exists=validate_session)
    app = FastAPI()
    register_routes(
        app,
        cast(
            Any,
            SimpleNamespace(
                approval_broker=broker,
                persistent_session_manager=manager,
                command_runtime=None,
            ),
        ),
    )
    return store, metadata, broker, TestClient(app)


@pytest.mark.parametrize("history_bytes", [0, 2 * 1024 * 1024])
def test_approval_read_never_loads_short_or_long_history(
    approval_sessions: tuple[JsonlSessionStore, SessionMetadataStore, ApprovalBroker, TestClient],
    monkeypatch: pytest.MonkeyPatch,
    history_bytes: int,
) -> None:
    store, metadata, _, client = approval_sessions
    if history_bytes:
        store.append_event(
            "session-1",
            Event(
                type="user_message",
                uuid="synthetic-message",
                ts=datetime.now(UTC),
                payload={"content": "x" * history_bytes},
            ),
        )
    metadata.compare_and_set_approval_mode("session-1", expected_version=0, mode="session_auto")

    def reject_load(_session_id: str) -> Session:
        pytest.fail("read-only approval query loaded conversation history")

    monkeypatch.setattr(store, "load", reject_load)
    response = client.get("/v1/sessions/session-1/approval-mode")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "approval_mode": "session_auto",
        "approval_mode_version": 1,
    }


def test_approval_read_rejects_missing_file_despite_metadata(
    approval_sessions: tuple[JsonlSessionStore, SessionMetadataStore, ApprovalBroker, TestClient],
) -> None:
    _, metadata, _, client = approval_sessions
    metadata.compare_and_set_approval_mode("missing", expected_version=0, mode="session_auto")

    response = client.get("/v1/sessions/missing/approval-mode")

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


def test_corrupt_history_remains_readable_only_as_approval_metadata(
    approval_sessions: tuple[JsonlSessionStore, SessionMetadataStore, ApprovalBroker, TestClient],
) -> None:
    store, metadata, _, client = approval_sessions
    (store.base_dir / "session-1.jsonl").write_text("not valid json\n", encoding="utf-8")

    response = client.get("/v1/sessions/session-1/approval-mode")

    assert response.status_code == 200
    assert response.json()["approval_mode"] == "per_request"
    with pytest.raises(SessionCorruptError):
        store.load("session-1")
    with pytest.raises(SessionCorruptError):
        client.put(
            "/v1/sessions/session-1/approval-mode",
            json={"mode": "session_auto", "expected_version": 0, "risk_acknowledged": True},
        )
    assert metadata.get("session-1").approval_mode_version == 0


def test_approval_read_does_not_disguise_storage_failure_as_missing(
    approval_sessions: tuple[JsonlSessionStore, SessionMetadataStore, ApprovalBroker, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, client = approval_sessions

    def fail_exists(_session_id: str) -> bool:
        raise SessionPersistError("synthetic attribute access failure")

    monkeypatch.setattr(store, "exists", fail_exists)
    with pytest.raises(SessionPersistError, match="synthetic attribute access failure"):
        client.get("/v1/sessions/session-1/approval-mode")


def test_combined_approval_keeps_history_validation_before_granting(
    approval_sessions: tuple[JsonlSessionStore, SessionMetadataStore, ApprovalBroker, TestClient],
) -> None:
    store, metadata, broker, client = approval_sessions
    request = PendingCommandApproval.create(
        session_id="session-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="synthetic approval",
        details=CommandApprovalDetails(
            cmd="synthetic.exe",
            argv=("synthetic.exe",),
            execution="host_user",
            workdir="C:/synthetic",
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
    pending = threading.Event()
    broker.add_listener(lambda _event: pending.set())
    decisions: list[Any] = []
    worker = threading.Thread(
        target=lambda: decisions.append(broker.request(request, timeout_seconds=5))
    )
    worker.start()
    try:
        assert pending.wait(timeout=2)
        (store.base_dir / "session-1.jsonl").write_text("not valid json\n", encoding="utf-8")
        with pytest.raises(SessionCorruptError):
            client.post(
                f"/v1/approvals/{request.request_id}/enable-session-auto",
                json={
                    "session_id": "session-1",
                    "run_id": "run-1",
                    "expected_request_version": 1,
                    "expected_mode_version": 0,
                    "risk_acknowledged": True,
                },
            )
        assert broker.pending() == (request,)
        assert metadata.get("session-1").approval_mode_version == 0
        assert decisions == []
    finally:
        broker.cancel_run("session-1", "run-1")
        worker.join(timeout=2)
    assert not worker.is_alive()


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
        cast(Any, SimpleNamespace(approval_broker=broker, command_runtime=None)),
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
                approval_broker=ApprovalBroker(),
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
                approval_broker=broker,
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
                approval_broker=broker,
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


def test_session_approval_mode_requires_acknowledgement_and_version(tmp_path: Path) -> None:
    broker = ApprovalBroker(
        mode_store=SessionMetadataStore(tmp_path / "metadata.json"),
        session_exists=lambda session_id: session_id == "session-1",
    )
    app = FastAPI()
    register_routes(
        app,
        cast(Any, SimpleNamespace(approval_broker=broker, command_runtime=None)),
    )
    client = TestClient(app)

    missing_ack = client.put(
        "/v1/sessions/session-1/approval-mode",
        json={"mode": "session_auto", "expected_version": 0},
    )
    enabled = client.put(
        "/v1/sessions/session-1/approval-mode",
        json={
            "mode": "session_auto",
            "expected_version": 0,
            "risk_acknowledged": True,
        },
    )
    current = client.get("/v1/sessions/session-1/approval-mode")
    stale = client.put(
        "/v1/sessions/session-1/approval-mode",
        json={"mode": "per_request", "expected_version": 0},
    )
    disabled = client.put(
        "/v1/sessions/session-1/approval-mode",
        json={"mode": "per_request", "expected_version": 1},
    )

    assert missing_ack.status_code == 422
    assert enabled.json()["approval_mode"] == "session_auto"
    assert enabled.json()["approval_mode_version"] == 1
    assert current.json() == {
        "session_id": "session-1",
        "approval_mode": "session_auto",
        "approval_mode_version": 1,
    }
    assert stale.status_code == 409
    assert stale.json()["detail"]["approval_mode_version"] == 1
    assert disabled.json()["approval_mode"] == "per_request"
    assert disabled.json()["approval_mode_version"] == 2


def test_card_combination_approves_exact_request_and_switches_mode(tmp_path: Path) -> None:
    broker = ApprovalBroker(
        mode_store=SessionMetadataStore(tmp_path / "metadata.json"),
        session_exists=lambda session_id: session_id == "session-1",
    )
    request = PendingCommandApproval.create(
        session_id="session-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="launch desktop app",
        details=CommandApprovalDetails(
            cmd="app.exe",
            argv=("app.exe",),
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
    decisions: list[Any] = []
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
        cast(Any, SimpleNamespace(approval_broker=broker, command_runtime=None)),
    )

    response = TestClient(app).post(
        f"/v1/approvals/{request.request_id}/enable-session-auto",
        json={
            "session_id": "session-1",
            "run_id": "run-1",
            "expected_request_version": 1,
            "expected_mode_version": 0,
            "risk_acknowledged": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["approval_mode"] == "session_auto"
    worker.join(timeout=2)
    assert decisions and decisions[0].approved is True
    assert request.decision_source == "user_enable_session_auto"
