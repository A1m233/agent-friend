"""voice_bridge 进程配置项。

使用 :mod:`pydantic_settings` 从环境变量 / ``.env`` 加载。

字段分两类：

- ``VOICE_BRIDGE_*``：voice_bridge 自身的设置（host / port / public_url 等）
- ``VOLC_*``：复用 spike 已有的火山引擎凭证（不加 ``VOICE_BRIDGE_`` 前缀，
  通过 pydantic ``validation_alias`` 显式绑定）

其中 ``VOICE_BRIDGE_PUBLIC_URL`` 是 cloudflared / ngrok 等 tunnel 生成的临时
运行态值，只允许来自当前进程环境变量，不从 ``.env`` 读取，避免旧 URL 污染实机验证。

详见 docs/requirements/007-voice-call/design.md §4.2.1。
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

_DOTENV_RUNTIME_ONLY_KEYS = frozenset(
    {
        "public_url",
        "VOICE_BRIDGE_PUBLIC_URL",
        "voice_bridge_public_url",
    }
)


class _FilteredDotenvSource(PydanticBaseSettingsSource):
    """Wrap pydantic's dotenv source while dropping runtime-only keys."""

    def __init__(
        self,
        source: PydanticBaseSettingsSource,
        ignored_keys: frozenset[str],
    ) -> None:
        super().__init__(source.settings_cls)
        self._source = source
        self._ignored_keys = ignored_keys

    def _set_current_state(self, state: dict[str, Any]) -> None:
        super()._set_current_state(state)
        self._source._set_current_state(state)

    def _set_settings_sources_data(self, states: dict[str, dict[str, Any]]) -> None:
        super()._set_settings_sources_data(states)
        self._source._set_settings_sources_data(states)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        data = self._source()
        return {key: value for key, value in data.items() if key not in self._ignored_keys}


class VoiceBridgeSettings(BaseSettings):
    """voice_bridge 启动配置。

    所有字段都有合理默认值（除火山凭证）；缺火山凭证时控制平面调火山 OpenAPI
    会失败，但 voice_bridge 进程本身能起来——便于本地仅跑单元测试时不强求
    填齐 ``.env``。
    """

    model_config = SettingsConfigDict(
        env_prefix="VOICE_BRIDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _FilteredDotenvSource(dotenv_settings, _DOTENV_RUNTIME_ONLY_KEYS),
            file_secret_settings,
        )

    host: str = "127.0.0.1"
    port: int = 18900
    log_level: str = "INFO"

    public_url: str = ""
    """voice_bridge 对火山 RTC 云端可见的公网 URL 前缀（如 cloudflared URL）。

    生产前必须设置，否则火山 RTC 在 ``LLMConfig.Url`` 里拿到的就是 127.0.0.1，
    云端无法回调。开发期仅跑 mock 测试时可空。

    通过 ``VOICE_BRIDGE_PUBLIC_URL`` 环境变量覆盖；不会从 ``.env`` 读取。
    """

    agent_bridge_url: str = "http://127.0.0.1:18800"
    """agent_bridge 的本机回环地址；voice_bridge LLM proxy 把流量转发到这里。"""

    # ----- 火山引擎凭证（沿用 spike 已有 .env 变量名，无 VOICE_BRIDGE_ 前缀） -----

    volc_access_key: str = Field(
        default="",
        validation_alias=AliasChoices("VOLC_ACCESS_KEY", "VOICE_BRIDGE_VOLC_ACCESS_KEY"),
    )
    volc_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("VOLC_SECRET_KEY", "VOICE_BRIDGE_VOLC_SECRET_KEY"),
    )
    volc_rtc_app_id: str = Field(
        default="",
        validation_alias=AliasChoices("VOLC_RTC_APP_ID", "VOICE_BRIDGE_VOLC_RTC_APP_ID"),
    )
    volc_rtc_app_key: str = Field(
        default="",
        validation_alias=AliasChoices("VOLC_RTC_APP_KEY", "VOICE_BRIDGE_VOLC_RTC_APP_KEY"),
    )
    volc_speech_app_id: str = Field(
        default="",
        validation_alias=AliasChoices("VOLC_SPEECH_APP_ID", "VOICE_BRIDGE_VOLC_SPEECH_APP_ID"),
    )
    volc_speech_access_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "VOLC_SPEECH_ACCESS_TOKEN", "VOICE_BRIDGE_VOLC_SPEECH_ACCESS_TOKEN"
        ),
    )
    volc_speech_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("VOLC_SPEECH_API_KEY", "VOICE_BRIDGE_VOLC_SPEECH_API_KEY"),
    )
    """火山语音新版控制台的 APP Key。为空时使用旧控制台 App ID + Access Token。"""

    volc_speech_resource_id: str = Field(
        default="volc.bigasr.sauc.duration",
        validation_alias=AliasChoices(
            "VOLC_SPEECH_RESOURCE_ID", "VOICE_BRIDGE_VOLC_SPEECH_RESOURCE_ID"
        ),
    )
    """火山流式 ASR 资源 ID；默认使用官方文档示例中的 1.0 小时版。"""

    volc_speech_ws_url: str = Field(
        default="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        validation_alias=AliasChoices("VOLC_SPEECH_WS_URL", "VOICE_BRIDGE_VOLC_SPEECH_WS_URL"),
    )
    """火山流式 ASR WebSocket 入口；默认使用双向流式优化版。"""

    voice_input_prewarm_enabled: bool = True
    """Whether chat composer voice input may open a connect-only ASR warm socket."""

    voice_input_prewarm_ttl_ms: int = Field(default=30_000, ge=5_000, le=300_000)
    """Maximum age for a connect-only ASR warm socket before it is closed."""

    # ----- 通话默认参数 -----

    voice_type: str = "zh_female_linjianvhai_moon_bigtts"
    """火山 TTS 音色 id；spike 实测自然，沿用。"""

    welcome_message: str = ""
    """通话拨通后 AI 主动说的第一句话；空字符串表示不开场白。"""

    default_persona: str = "default"
    """新建 session 时的默认 persona。"""

    default_model: str = ""
    """新建 session 时的默认 model；空字符串表示让 agent_bridge 自己决定。"""
