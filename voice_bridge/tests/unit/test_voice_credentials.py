"""Voice credential boundary tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from voice_bridge.app import create_app
from voice_bridge.settings import VoiceBridgeSettings


def test_start_call_fails_fast_when_voice_credentials_missing() -> None:
    client = TestClient(create_app(VoiceBridgeSettings(public_url="https://test.example.com")))

    resp = client.post("/voice/calls", json={})

    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["error"] == "voice_credentials_missing"
    assert "凭据" in body["detail"]["message"]
    assert "AKLT" not in resp.text
