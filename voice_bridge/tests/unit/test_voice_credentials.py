"""Voice credential boundary tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from voice_bridge.app import create_app, create_app_with_runtime
from voice_bridge.assembly import build_runtime
from voice_bridge.settings import VoiceBridgeSettings


def test_start_call_fails_fast_when_voice_credentials_missing() -> None:
    client = TestClient(
        create_app(
            VoiceBridgeSettings(
                public_url="https://test.example.com",
                volc_access_key="",
                volc_secret_key="",
                volc_rtc_app_id="",
                volc_rtc_app_key="",
                volc_speech_app_id="",
                volc_speech_access_token="",
                volc_speech_api_key="",
            )
        )
    )

    resp = client.post("/voice/calls", json={})

    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["error"] == "voice_credentials_missing"
    assert "凭据" in body["detail"]["message"]
    assert "AKLT" not in resp.text


def test_start_call_fails_fast_when_chat_credentials_missing() -> None:
    settings = VoiceBridgeSettings(
        public_url="https://test.example.com",
        volc_access_key="AKLT-test",
        volc_secret_key="test-secret",
        volc_rtc_app_id="rtc-app-id",
        volc_rtc_app_key="rtc-app-key",
        volc_speech_app_id="speech-app-id",
        volc_speech_access_token="speech-token",
    )
    # create_app(settings=...) is an injected path and intentionally has no source
    # preflight. Use the runtime-aware app helper to cover the production runtime field.
    client = TestClient(
        create_app_with_runtime(
            build_runtime(settings, missing_chat_credentials=lambda: ("KIMI_API_KEY",))
        )
    )

    resp = client.post("/voice/calls", json={})

    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["error"] == "chat_credentials_missing"
    assert "API Key" in body["detail"]["message"]
    assert "test-secret" not in resp.text
