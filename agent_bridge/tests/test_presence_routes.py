from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from agent_bridge.assembly import BridgeRuntime
from agent_bridge.presence import PresenceRegistry
from agent_bridge.routes.presence import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_desktop_presence_round_trip() -> None:
    observed_at = datetime.now(UTC)
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
            "observedAt": observed_at.isoformat(),
            "currentSessionId": "session-1",
            "windowVisible": True,
            "windowFocused": True,
            "windowMinimized": False,
            "osIdleMs": 500,
            "sessionState": "active",
            "lastDesktopInteractionAt": (observed_at - timedelta(milliseconds=500)).isoformat(),
            "capabilities": ["idle", "window"],
        },
    )
    assert response.status_code == 200
    snapshot = registry.snapshot(now=observed_at, pet_available=False)
    assert snapshot.desktop is not None
    assert snapshot.desktop.current_session_id == "session-1"
    diagnostics = client.get("/v1/runtime/reminder-diagnostics")
    assert diagnostics.status_code == 200
    presence = diagnostics.json()["presence"]
    assert presence["desktop"]["os_idle_ms"] == 500
    desktop_signal = presence["desktopSignal"]
    assert desktop_signal["fresh"] is True
    assert desktop_signal["ageMs"] < 1_000
    assert desktop_signal["eligible"] is True
    assert desktop_signal["idleBucket"] == "0-30s"
    assert desktop_signal["reason"] == "eligible"
    assert presence["previewDecision"]["kind"] == "desktop"
