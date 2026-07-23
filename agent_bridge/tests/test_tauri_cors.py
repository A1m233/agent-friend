"""Packaged Tauri WebView CORS boundary tests."""

from agent_bridge.app import TAURI_PRODUCTION_ORIGINS, _add_tauri_production_cors
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    app = FastAPI()
    _add_tauri_production_cors(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_tauri_production_origins_can_call_bridge() -> None:
    client = _client()
    for origin in TAURI_PRODUCTION_ORIGINS:
        response = client.get("/healthz", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin


def test_tauri_production_origin_can_preflight_mutation() -> None:
    response = _client().options(
        "/v1/persona",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,x-attachment-filename",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
    assert "x-attachment-filename" in response.headers["access-control-allow-headers"].lower()


def test_tauri_development_origin_can_call_bridge_directly() -> None:
    response = _client().options(
        "/healthz",
        headers={
            "Origin": "http://localhost:1420",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:1420"


def test_untrusted_web_origin_is_not_allowed() -> None:
    response = _client().get("/healthz", headers={"Origin": "https://example.com"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
