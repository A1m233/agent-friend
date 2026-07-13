"""``VoiceBridgeSettings`` 运行态配置边界单测。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import voice_bridge.__main__ as voice_main
from voice_bridge.settings import VoiceBridgeSettings


def test_default_settings_ignore_dotenv_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_FRIEND_CONFIG_MODE", raising=False)
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
    assert settings.port == 18900
    assert settings.volc_rtc_app_id == ""


def test_legacy_dotenv_keeps_other_dotenv_values_but_ignores_public_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_FRIEND_CONFIG_MODE", raising=False)
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

    settings = VoiceBridgeSettings(_env_file=".env")  # type: ignore[call-arg]

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
    monkeypatch.delenv("AGENT_FRIEND_CONFIG_MODE", raising=False)
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

    def fake_run(server: Any) -> None:
        captured["port"] = server.config.port

    monkeypatch.setattr("voice_bridge.__main__.uvicorn.Server.run", fake_run)

    assert voice_main.main([]) == 0
    assert "VOICE_BRIDGE_PUBLIC_URL" not in os.environ
    assert captured["port"] == 18900


def test_main_legacy_dotenv_reads_non_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_FRIEND_CONFIG_MODE", raising=False)
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

    def fake_run(server: Any) -> None:
        captured["port"] = server.config.port

    monkeypatch.setattr("voice_bridge.__main__.uvicorn.Server.run", fake_run)

    assert voice_main.main(["--legacy-dotenv"]) == 0
    assert "VOICE_BRIDGE_PUBLIC_URL" not in os.environ
    assert captured["port"] == 19999


def test_streaming_asr_defaults() -> None:
    settings = VoiceBridgeSettings()

    assert settings.volc_speech_resource_id == "volc.bigasr.sauc.duration"
    assert (
        settings.volc_speech_ws_url == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    )
    assert settings.voice_input_prewarm_enabled is True
    assert settings.voice_input_prewarm_ttl_ms == 30_000


def test_voice_call_latency_defaults() -> None:
    settings = VoiceBridgeSettings()

    assert settings.asr_vad_silence_time_ms == 1000
    assert settings.asr_vad_ai_vad_enabled is True
    assert settings.llm_prefill_enabled is False
    assert settings.tts_cluster == "volcano_tts"
    assert settings.voice_type == "zh_female_linjianvhai_moon_bigtts"


def test_voice_call_latency_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_BRIDGE_ASR_VAD_SILENCE_TIME_MS", "750")
    monkeypatch.setenv("VOICE_BRIDGE_ASR_VAD_AI_VAD_ENABLED", "false")
    monkeypatch.setenv("VOICE_BRIDGE_LLM_PREFILL_ENABLED", "true")
    monkeypatch.setenv("VOICE_BRIDGE_TTS_CLUSTER", "volcano_tts")
    monkeypatch.setenv("VOICE_BRIDGE_VOICE_TYPE", "BV002_streaming")

    settings = VoiceBridgeSettings()

    assert settings.asr_vad_silence_time_ms == 750
    assert settings.asr_vad_ai_vad_enabled is False
    assert settings.llm_prefill_enabled is True
    assert settings.tts_cluster == "volcano_tts"
    assert settings.voice_type == "BV002_streaming"
