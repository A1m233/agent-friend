"""voice_bridge runtime credential resolution tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import runtime_config.sources as sources
from voice_bridge.runtime_config import (
    apply_voice_credentials,
    missing_chat_credential_names,
    resolve_voice_credentials,
)
from voice_bridge.settings import VoiceBridgeSettings

from runtime_config import (
    KIMI_API_KEY,
    LLM_CHAT_PROVIDER,
    VOLC_SPEECH_API_KEY,
    credential_source_for_mode,
)


class FakeKeyring:
    def get_password(self, service: str, name: str) -> str | None:
        return None


def test_resolves_voice_bridge_speech_api_key_alias() -> None:
    source = credential_source_for_mode(
        "desktop",
        keyring_backend=FakeKeyring(),
        env={"VOICE_BRIDGE_VOLC_SPEECH_API_KEY": "speech-api-key"},
    )

    credentials = resolve_voice_credentials(source)
    settings = apply_voice_credentials(VoiceBridgeSettings(), credentials)

    assert credentials.volc_speech_api_key == "speech-api-key"
    assert settings.volc_speech_api_key == "speech-api-key"


def test_canonical_speech_api_key_still_resolves() -> None:
    source = credential_source_for_mode(
        "desktop",
        keyring_backend=FakeKeyring(),
        env={VOLC_SPEECH_API_KEY: "test-canonical-speech-api-key"},
    )

    credentials = resolve_voice_credentials(source)

    assert credentials.volc_speech_api_key == "test-canonical-speech-api-key"


def test_missing_chat_credentials_follow_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sources, "_legacy_rust_windows_keyring_value", lambda *_args: None)
    source = credential_source_for_mode(
        "desktop",
        settings_path=tmp_path / "missing-settings.json",
        keyring_backend=FakeKeyring(),
        env={LLM_CHAT_PROVIDER: "kimi"},
    )

    assert missing_chat_credential_names(source) == (KIMI_API_KEY,)


def test_configured_chat_credentials_pass_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sources, "_legacy_rust_windows_keyring_value", lambda *_args: None)
    source = credential_source_for_mode(
        "desktop",
        settings_path=tmp_path / "missing-settings.json",
        keyring_backend=FakeKeyring(),
        env={LLM_CHAT_PROVIDER: "kimi", KIMI_API_KEY: "test-kimi-key"},
    )

    assert missing_chat_credential_names(source) == ()
