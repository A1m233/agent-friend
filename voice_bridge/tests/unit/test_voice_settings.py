"""``VoiceBridgeSettings`` 运行态配置边界单测。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import voice_bridge.__main__ as voice_main
from voice_bridge.settings import VoiceBridgeSettings


def test_public_url_ignores_dotenv_but_keeps_other_dotenv_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "VOICE_BRIDGE_PUBLIC_URL=https://stale-tunnel.example.com",
                "VOICE_BRIDGE_PORT=19999",
                "VOLC_RTC_APP_ID=rtc-from-dotenv",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("VOICE_BRIDGE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("VOICE_BRIDGE_PORT", raising=False)
    monkeypatch.delenv("VOLC_RTC_APP_ID", raising=False)

    settings = VoiceBridgeSettings()

    assert settings.public_url == ""
    assert settings.port == 19999
    assert settings.volc_rtc_app_id == "rtc-from-dotenv"


def test_public_url_uses_explicit_process_env_over_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "VOICE_BRIDGE_PUBLIC_URL=https://stale-tunnel.example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOICE_BRIDGE_PUBLIC_URL", "https://fresh-tunnel.example.com")

    settings = VoiceBridgeSettings()

    assert settings.public_url == "https://fresh-tunnel.example.com"


def test_main_does_not_load_dotenv_into_process_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "VOICE_BRIDGE_PUBLIC_URL=https://stale-tunnel.example.com",
                "VOICE_BRIDGE_PORT=19999",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("VOICE_BRIDGE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("VOICE_BRIDGE_PORT", raising=False)

    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("voice_bridge.__main__.uvicorn.run", fake_run)

    assert voice_main.main() == 0
    assert "VOICE_BRIDGE_PUBLIC_URL" not in os.environ
    assert captured["kwargs"]["port"] == 19999


def test_streaming_asr_defaults() -> None:
    settings = VoiceBridgeSettings()

    assert settings.volc_speech_resource_id == "volc.bigasr.sauc.duration"
    assert (
        settings.volc_speech_ws_url == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    )
    assert settings.voice_input_prewarm_enabled is True
    assert settings.voice_input_prewarm_ttl_ms == 30_000
