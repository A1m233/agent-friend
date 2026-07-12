from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

from agent.runtime.scheduler import RuntimeActivitySnapshot, RuntimeDrainReport
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.lifecycle import BridgeLifecycleController, register_lifecycle_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


@dataclass
class FakeAgentRuntime:
    accepting: bool = True
    timed_out: bool = False

    def activity_snapshot(self) -> RuntimeActivitySnapshot:
        return RuntimeActivitySnapshot(self.accepting, 0, 0)

    def quiesce(self) -> RuntimeActivitySnapshot:
        self.accepting = False
        return self.activity_snapshot()

    def drain(self, *, grace_timeout: float, cancel_timeout: float) -> RuntimeDrainReport:
        self.accepting = False
        return RuntimeDrainReport(1, 1, 0, 1, int(self.timed_out), self.timed_out)


def make_client(runtime: FakeAgentRuntime, shutdown: list[str]) -> TestClient:
    app = FastAPI()
    bridge_runtime = cast(BridgeRuntime, SimpleNamespace(agent_runtime=runtime))
    controller = BridgeLifecycleController(
        bridge_runtime,
        token="secret-token",
        shutdown_callback=lambda: shutdown.append("called"),
    )
    register_lifecycle_routes(app, controller)
    return TestClient(app)


def test_lifecycle_routes_require_token_without_echoing_it() -> None:
    client = make_client(FakeAgentRuntime(), [])
    assert client.get("/internal/lifecycle/readiness").status_code == 401
    response = client.get(
        "/internal/lifecycle/readiness",
        headers={"X-Agent-Friend-Lifecycle-Token": "wrong-secret"},
    )
    assert response.status_code == 401
    assert "wrong-secret" not in response.text


def test_readiness_and_quiesce_are_distinct() -> None:
    runtime = FakeAgentRuntime()
    client = make_client(runtime, [])
    headers = {"X-Agent-Friend-Lifecycle-Token": "secret-token"}
    assert client.get("/internal/lifecycle/readiness", headers=headers).json()["status"] == "ready"
    assert client.post("/internal/lifecycle/quiesce", headers=headers).json()["accepting"] is False
    assert (
        client.get("/internal/lifecycle/readiness", headers=headers).json()["status"] == "not_ready"
    )


def test_shutdown_calls_callback_only_after_successful_drain() -> None:
    shutdown: list[str] = []
    runtime = FakeAgentRuntime()
    client = make_client(runtime, shutdown)
    headers = {"X-Agent-Friend-Lifecycle-Token": "secret-token"}
    response = client.post(
        "/internal/lifecycle/shutdown",
        headers=headers,
        json={"grace_timeout": 0, "cancel_timeout": 0},
    )
    assert response.status_code == 200
    assert shutdown == ["called"]

    shutdown.clear()
    runtime.timed_out = True
    response = client.post(
        "/internal/lifecycle/shutdown",
        headers=headers,
        json={"grace_timeout": 0, "cancel_timeout": 0},
    )
    assert response.status_code == 409
    assert shutdown == []
