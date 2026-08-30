from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

from agent.runtime import RuntimeInstanceIdentity, RuntimeStartReason
from agent.runtime.scheduler import RuntimeActivitySnapshot, RuntimeDrainReport
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.lifecycle import BridgeLifecycleController, register_lifecycle_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


@dataclass
class FakeAgentRuntime:
    accepting: bool = True
    timed_out: bool = False
    queued: int = 0
    running: int = 0

    def activity_snapshot(self) -> RuntimeActivitySnapshot:
        return RuntimeActivitySnapshot(self.accepting, self.queued, self.running)

    def quiesce(self) -> RuntimeActivitySnapshot:
        self.accepting = False
        return self.activity_snapshot()

    def drain(self, *, grace_timeout: float, cancel_timeout: float) -> RuntimeDrainReport:
        self.accepting = False
        return RuntimeDrainReport(1, 1, 0, 1, int(self.timed_out), self.timed_out)


def make_client(
    runtime: FakeAgentRuntime,
    shutdown: list[str],
    *,
    im_active: bool = False,
    im_binding_count: int = 0,
) -> TestClient:
    app = FastAPI()
    im_runtime = SimpleNamespace(
        desired_online_snapshot=lambda: SimpleNamespace(
            active=im_active,
            binding_count=im_binding_count,
        )
    )
    bridge_runtime = cast(
        BridgeRuntime,
        SimpleNamespace(
            agent_runtime=runtime,
            im_runtime=im_runtime,
            runtime_identity=RuntimeInstanceIdentity.create(
                supervisor_generation=7,
                start_reason=RuntimeStartReason.DEFAULT_MODEL_CHANGED,
            ),
        ),
    )
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
    readiness = client.get("/internal/lifecycle/readiness", headers=headers).json()
    assert readiness["status"] == "ready"
    assert readiness["runtime"]["supervisorGeneration"] == 7
    assert readiness["runtime"]["startReason"] == "default_model_changed"
    assert readiness["runtime"]["bridgeInstanceId"]
    assert client.post("/internal/lifecycle/quiesce", headers=headers).json()["accepting"] is False
    assert (
        client.get("/internal/lifecycle/readiness", headers=headers).json()["status"] == "not_ready"
    )


def test_background_demand_projects_agent_and_im_authorities() -> None:
    runtime = FakeAgentRuntime(queued=2, running=1)
    client = make_client(runtime, [], im_active=True, im_binding_count=3)
    headers = {"X-Agent-Friend-Lifecycle-Token": "secret-token"}

    response = client.get("/internal/lifecycle/background-demand", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "agentTask": {"active": True, "queued": 2, "running": 1},
        "imOnline": {"active": True, "bindingCount": 3},
    }


def test_background_demand_requires_owner_token() -> None:
    client = make_client(FakeAgentRuntime(), [])
    assert client.get("/internal/lifecycle/background-demand").status_code == 401


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
