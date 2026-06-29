"""Runtime credential resolution for voice_bridge."""

from __future__ import annotations

from runtime_config import (
    VOLC_ACCESS_KEY,
    VOLC_RTC_APP_ID,
    VOLC_RTC_APP_KEY,
    VOLC_SECRET_KEY,
    VOLC_SPEECH_ACCESS_TOKEN,
    VOLC_SPEECH_API_KEY,
    VOLC_SPEECH_APP_ID,
    CredentialSource,
    VoiceCredentialConfig,
    optional_secret,
)

from .settings import VoiceBridgeSettings


def resolve_voice_credentials(source: CredentialSource) -> VoiceCredentialConfig:
    """Resolve Volc voice credentials from configured runtime sources."""

    return VoiceCredentialConfig.from_mapping(
        {
            VOLC_ACCESS_KEY: optional_secret(source, VOLC_ACCESS_KEY),
            VOLC_SECRET_KEY: optional_secret(source, VOLC_SECRET_KEY),
            VOLC_RTC_APP_ID: optional_secret(source, VOLC_RTC_APP_ID),
            VOLC_RTC_APP_KEY: optional_secret(source, VOLC_RTC_APP_KEY),
            VOLC_SPEECH_APP_ID: optional_secret(source, VOLC_SPEECH_APP_ID),
            VOLC_SPEECH_ACCESS_TOKEN: optional_secret(source, VOLC_SPEECH_ACCESS_TOKEN),
            VOLC_SPEECH_API_KEY: optional_secret(source, VOLC_SPEECH_API_KEY),
        }
    )


def apply_voice_credentials(
    settings: VoiceBridgeSettings,
    credentials: VoiceCredentialConfig,
) -> VoiceBridgeSettings:
    """Return settings with explicitly resolved voice credentials applied."""

    return settings.model_copy(update=credentials.as_settings_update())


def credentials_from_settings(settings: VoiceBridgeSettings) -> VoiceCredentialConfig:
    """Extract a credential bundle from already-built settings."""

    return VoiceCredentialConfig(
        volc_access_key=settings.volc_access_key,
        volc_secret_key=settings.volc_secret_key,
        volc_rtc_app_id=settings.volc_rtc_app_id,
        volc_rtc_app_key=settings.volc_rtc_app_key,
        volc_speech_app_id=settings.volc_speech_app_id,
        volc_speech_access_token=settings.volc_speech_access_token,
        volc_speech_api_key=settings.volc_speech_api_key,
    )
