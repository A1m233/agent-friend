"""Credential names and typed runtime credential bundles."""

from __future__ import annotations

from dataclasses import dataclass

AMAP_WEBSERVICE_KEY = "AMAP_WEBSERVICE_KEY"
DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
DEEPSEEK_MODEL = "DEEPSEEK_MODEL"
DEEPSEEK_MEMORY_MODEL = "DEEPSEEK_MEMORY_MODEL"
KIMI_API_KEY = "KIMI_API_KEY"
LLM_CHAT_PROVIDER = "LLM_CHAT_PROVIDER"
LLM_CHAT_MODEL = "LLM_CHAT_MODEL"
LLM_MEMORY_PROVIDER = "LLM_MEMORY_PROVIDER"
LLM_MEMORY_MODEL = "LLM_MEMORY_MODEL"
TAVILY_API_KEY = "TAVILY_API_KEY"
VOLC_ACCESS_KEY = "VOLC_ACCESS_KEY"
VOLC_SECRET_KEY = "VOLC_SECRET_KEY"
VOLC_RTC_APP_ID = "VOLC_RTC_APP_ID"
VOLC_RTC_APP_KEY = "VOLC_RTC_APP_KEY"
VOLC_SPEECH_APP_ID = "VOLC_SPEECH_APP_ID"
VOLC_SPEECH_ACCESS_TOKEN = "VOLC_SPEECH_ACCESS_TOKEN"
VOLC_SPEECH_API_KEY = "VOLC_SPEECH_API_KEY"

VOICE_BRIDGE_PUBLIC_URL = "VOICE_BRIDGE_PUBLIC_URL"

CREDENTIAL_ALIASES: dict[str, tuple[str, ...]] = {
    LLM_CHAT_MODEL: (DEEPSEEK_MODEL,),
    LLM_MEMORY_MODEL: (DEEPSEEK_MEMORY_MODEL,),
    VOLC_ACCESS_KEY: ("VOICE_BRIDGE_VOLC_ACCESS_KEY",),
    VOLC_SECRET_KEY: ("VOICE_BRIDGE_VOLC_SECRET_KEY",),
    VOLC_RTC_APP_ID: ("VOICE_BRIDGE_VOLC_RTC_APP_ID",),
    VOLC_RTC_APP_KEY: ("VOICE_BRIDGE_VOLC_RTC_APP_KEY",),
    VOLC_SPEECH_APP_ID: ("VOICE_BRIDGE_VOLC_SPEECH_APP_ID",),
    VOLC_SPEECH_ACCESS_TOKEN: ("VOICE_BRIDGE_VOLC_SPEECH_ACCESS_TOKEN",),
    VOLC_SPEECH_API_KEY: ("VOICE_BRIDGE_VOLC_SPEECH_API_KEY",),
}

ALL_CREDENTIAL_NAMES: tuple[str, ...] = (
    AMAP_WEBSERVICE_KEY,
    DEEPSEEK_API_KEY,
    KIMI_API_KEY,
    TAVILY_API_KEY,
    VOLC_ACCESS_KEY,
    VOLC_SECRET_KEY,
    VOLC_RTC_APP_ID,
    VOLC_RTC_APP_KEY,
    VOLC_SPEECH_APP_ID,
    VOLC_SPEECH_ACCESS_TOKEN,
    VOLC_SPEECH_API_KEY,
)

# The newer Volc speech APP Key is optional while the legacy App ID + Access
# Token authentication path remains supported by voice_bridge.
OPTIONAL_CREDENTIAL_NAMES = frozenset({AMAP_WEBSERVICE_KEY, VOLC_SPEECH_API_KEY})
REQUIRED_CREDENTIAL_NAMES: tuple[str, ...] = tuple(
    name for name in ALL_CREDENTIAL_NAMES if name not in OPTIONAL_CREDENTIAL_NAMES
)

RUNTIME_ONLY_NAMES = frozenset({VOICE_BRIDGE_PUBLIC_URL})

CREDENTIAL_BUNDLES: dict[str, tuple[str, ...]] = {
    "amap": (AMAP_WEBSERVICE_KEY,),
    "deepseek": (DEEPSEEK_API_KEY,),
    "kimi": (KIMI_API_KEY,),
    "tavily": (TAVILY_API_KEY,),
    "volc-voice": (
        VOLC_ACCESS_KEY,
        VOLC_SECRET_KEY,
        VOLC_RTC_APP_ID,
        VOLC_RTC_APP_KEY,
        VOLC_SPEECH_APP_ID,
        VOLC_SPEECH_ACCESS_TOKEN,
        VOLC_SPEECH_API_KEY,
    ),
}


@dataclass(frozen=True)
class CredentialValue:
    """Resolved credential value plus its source label."""

    name: str
    value: str
    source: str


def credential_lookup_names(name: str) -> tuple[str, ...]:
    """Return canonical name plus process-env/dotenv aliases for one credential."""

    return (name, *CREDENTIAL_ALIASES.get(name, ()))


@dataclass(frozen=True)
class CredentialStatus:
    """Credential status safe to show in logs or UI."""

    name: str
    configured: bool
    source: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ToolCredentials:
    """Optional tool credentials for agent runtime assembly."""

    tavily_api_key: str | None = None
    amap_webservice_key: str | None = None


@dataclass(frozen=True)
class VoiceCredentialConfig:
    """Volc credentials required by real voice calls."""

    volc_access_key: str = ""
    volc_secret_key: str = ""
    volc_rtc_app_id: str = ""
    volc_rtc_app_key: str = ""
    volc_speech_app_id: str = ""
    volc_speech_access_token: str = ""
    volc_speech_api_key: str = ""

    @classmethod
    def from_mapping(cls, values: dict[str, str | None]) -> VoiceCredentialConfig:
        return cls(
            volc_access_key=values.get(VOLC_ACCESS_KEY) or "",
            volc_secret_key=values.get(VOLC_SECRET_KEY) or "",
            volc_rtc_app_id=values.get(VOLC_RTC_APP_ID) or "",
            volc_rtc_app_key=values.get(VOLC_RTC_APP_KEY) or "",
            volc_speech_app_id=values.get(VOLC_SPEECH_APP_ID) or "",
            volc_speech_access_token=values.get(VOLC_SPEECH_ACCESS_TOKEN) or "",
            volc_speech_api_key=values.get(VOLC_SPEECH_API_KEY) or "",
        )

    def as_settings_update(self) -> dict[str, str]:
        return {
            "volc_access_key": self.volc_access_key,
            "volc_secret_key": self.volc_secret_key,
            "volc_rtc_app_id": self.volc_rtc_app_id,
            "volc_rtc_app_key": self.volc_rtc_app_key,
            "volc_speech_app_id": self.volc_speech_app_id,
            "volc_speech_access_token": self.volc_speech_access_token,
            "volc_speech_api_key": self.volc_speech_api_key,
        }

    def missing_names(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.volc_access_key:
            missing.append(VOLC_ACCESS_KEY)
        if not self.volc_secret_key:
            missing.append(VOLC_SECRET_KEY)
        if not self.volc_rtc_app_id:
            missing.append(VOLC_RTC_APP_ID)
        if not self.volc_rtc_app_key:
            missing.append(VOLC_RTC_APP_KEY)
        if not self.volc_speech_app_id:
            missing.append(VOLC_SPEECH_APP_ID)
        if not self.volc_speech_access_token:
            missing.append(VOLC_SPEECH_ACCESS_TOKEN)
        return tuple(missing)
