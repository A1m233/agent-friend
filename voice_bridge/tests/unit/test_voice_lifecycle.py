from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from voice_bridge.assembly import VoiceBridgeRuntime
from voice_bridge.calls import CallBinding, CallRegistry, CallState
from voice_bridge.lifecycle import VoiceLifecycleController, register_lifecycle_routes


def make_client(registry: CallRegistry, shutdown: list[str]) -> TestClient:
    app = FastAPI()
    runtime = cast(VoiceBridgeRuntime, SimpleNamespace(call_registry=registry))
    controller = VoiceLifecycleController(
        runtime,
        token="secret-token",
        shutdown_callback=lambda: shutdown.append("called"),
    )
    register_lifecycle_routes(app, controller)
    return TestClient(app)


def binding(call_id: str, state: CallState) -> CallBinding:
    item = CallBinding(
        call_id=call_id,
        session_id="session",
        state="active",
        started_at=CallRegistry.now(),
        room_id="room",
        bot_user_id="bot",
        target_user_id="user",
    )
    return replace(item, state=state)


def test_voice_lifecycle_requires_token() -> None:
    client = make_client(CallRegistry(), [])
    assert client.get("/internal/lifecycle/readiness").status_code == 401


def test_pending_and_active_calls_block_shutdown() -> None:
    for state in ("pending", "active"):
        registry = CallRegistry()
        registry.bind(binding(state, state))
        shutdown: list[str] = []
        client = make_client(registry, shutdown)
        response = client.post(
            "/internal/lifecycle/shutdown",
            headers={"X-Agent-Friend-Lifecycle-Token": "secret-token"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "voice_activity_blocked"
        assert shutdown == []


def test_inactive_calls_allow_shutdown() -> None:
    registry = CallRegistry()
    registry.bind(binding("stopped", "stopped"))
    registry.bind(binding("error", "error"))
    shutdown: list[str] = []
    client = make_client(registry, shutdown)
    response = client.post(
        "/internal/lifecycle/shutdown",
        headers={"X-Agent-Friend-Lifecycle-Token": "secret-token"},
    )
    assert response.status_code == 200
    assert shutdown == ["called"]
