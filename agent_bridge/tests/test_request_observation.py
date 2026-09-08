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
