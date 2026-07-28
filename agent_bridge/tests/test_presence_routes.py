from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from agent_bridge.assembly import BridgeRuntime
from agent_bridge.presence import PresenceRegistry
from agent_bridge.routes.presence import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_desktop_presence_round_trip() -> None:
    registry = PresenceRegistry()
    runtime = cast(
        BridgeRuntime,
        SimpleNamespace(
            presence_registry=registry,
            agent_runtime=None,
            todo_store=None,
        ),
    )
    app = FastAPI()
    register_routes(app, runtime)
    client = TestClient(app)
    response = client.post(
        "/v1/runtime/presence/desktop",
        json={
            "observedAt": "2026-07-28T01:00:00Z",
            "currentSessionId": "session-1",
            "windowVisible": True,
            "windowFocused": True,
            "windowMinimized": False,
            "osIdleMs": 500,
            "sessionState": "active",
            "lastDesktopInteractionAt": "2026-07-28T00:59:59.500Z",
            "capabilities": ["idle", "window"],
        },
    )
    assert response.status_code == 200
    snapshot = registry.snapshot(now=datetime(2026, 7, 28, 1, tzinfo=UTC), pet_available=False)
    assert snapshot.desktop is not None
    assert snapshot.desktop.current_session_id == "session-1"
    diagnostics = client.get("/v1/runtime/reminder-diagnostics")
    assert diagnostics.status_code == 200
    presence = diagnostics.json()["presence"]
    assert presence["desktop"]["os_idle_ms"] == 500
    assert presence["desktopSignal"] == {
        "fresh": True,
        "ageMs": 0,
        "eligible": True,
        "idleBucket": "0-30s",
        "reason": "eligible",
    }
    assert presence["previewDecision"]["kind"] == "desktop"
