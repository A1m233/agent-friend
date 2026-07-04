"""voice_bridge runtime credential resolution tests."""

from __future__ import annotations

from voice_bridge.runtime_config import apply_voice_credentials, resolve_voice_credentials
from voice_bridge.settings import VoiceBridgeSettings

from runtime_config import VOLC_SPEECH_API_KEY, credential_source_for_mode


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
