"""CORS 配置单测（smoke 页跨端口 fetch 依赖）。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from voice_bridge.app import create_app
from voice_bridge.settings import VoiceBridgeSettings


def test_options_preflight_allows_smoke_origin() -> None:
    settings = VoiceBridgeSettings(
        public_url="https://test.example.com",
        volc_access_key="ak",
        volc_secret_key="sk",
        volc_rtc_app_id="rtc",
        volc_rtc_app_key="key",
        volc_speech_app_id="speech",
        volc_speech_access_token="token",
    )
    client = TestClient(create_app(settings))
    resp = client.options(
        "/voice/calls",
        headers={
            "Origin": "http://127.0.0.1:8765",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:8765"


def test_options_preflight_allows_tauri_origin() -> None:
    settings = VoiceBridgeSettings(
        public_url="https://test.example.com",
        volc_access_key="ak",
        volc_secret_key="sk",
        volc_rtc_app_id="rtc",
        volc_rtc_app_key="key",
        volc_speech_app_id="speech",
        volc_speech_access_token="token",
    )
    client = TestClient(create_app(settings))
    for origin in ("http://tauri.localhost", "https://tauri.localhost"):
        resp = client.options(
            "/voice/calls",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == origin
