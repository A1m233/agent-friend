from __future__ import annotations

import logging

import pytest
from agent.sessions.file_observation import session_request_id
from agent_bridge.request_observation import RequestObservationMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_sync_request_context_is_correlated_and_log_does_not_capture_user_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    app.add_middleware(RequestObservationMiddleware)

    @app.get("/sessions/{session_id}")
    def endpoint(session_id: str) -> dict[str, str]:
        return {"request": session_request_id.get()}

    caplog.set_level(logging.DEBUG, logger="agent_bridge.request_observation")
    with TestClient(app) as client:
        a = client.get("/sessions/PRIVATE_SESSION?secret=PRIVATE_QUERY")
        b = client.get("/sessions/OTHER")
    assert a.headers["x-agent-friend-request-id"] == a.json()["request"]
    assert a.json()["request"] != b.json()["request"]
    assert session_request_id.get() == ""
    messages = "\n".join(
        r.message for r in caplog.records if r.name == "agent_bridge.request_observation"
    )
    assert "route=/sessions/{session_id}" in messages
    assert "PRIVATE_SESSION" not in messages
    assert "PRIVATE_QUERY" not in messages


def test_observation_failure_does_not_change_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge import request_observation

    app = FastAPI()
    app.add_middleware(RequestObservationMiddleware)

    @app.get("/ok")
    def endpoint() -> dict[str, bool]:
        return {"ok": True}

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("logging failure")

    monkeypatch.setattr(request_observation.logger, "log", fail)
    assert TestClient(app).get("/ok").json() == {"ok": True}


def test_navigation_correlation_rejects_arbitrary_header_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    app.add_middleware(RequestObservationMiddleware)

    @app.get("/ok")
    def endpoint() -> dict[str, bool]:
        return {"ok": True}

    caplog.set_level(logging.DEBUG, logger="agent_bridge.request_observation")
    with TestClient(app) as client:
        assert (
            client.get("/ok", headers={"x-agent-friend-navigation-id": "nav-123"}).status_code
            == 200
        )
        assert (
            client.get(
                "/ok", headers={"x-agent-friend-navigation-id": "/private/path?secret=yes"}
            ).status_code
            == 200
        )
    messages = "\n".join(record.message for record in caplog.records)
    assert "navigation_id=nav-123" in messages
    assert "navigation_id=None" in messages
    assert "private/path" not in messages


def test_desktop_cors_allows_navigation_and_exposes_request_identity() -> None:
    from agent_bridge.app import _add_tauri_production_cors

    app = FastAPI()
    app.add_middleware(RequestObservationMiddleware)
    _add_tauri_production_cors(app)

    @app.get("/ok")
    def endpoint() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        preflight = client.options(
            "/ok",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-agent-friend-navigation-id",
            },
        )
        assert preflight.status_code == 200
        response = client.get("/ok", headers={"Origin": "http://tauri.localhost"})
    assert response.headers["x-agent-friend-request-id"]
    assert "x-agent-friend-request-id" in response.headers["access-control-expose-headers"].lower()
