"""voice_bridge 进程配置项。

使用 :mod:`pydantic_settings` 从环境变量 / ``.env`` 加载。

字段分两类：

- ``VOICE_BRIDGE_*``：voice_bridge 自身的设置（host / port / public_url 等）
- ``VOLC_*``：复用 spike 已有的火山引擎凭证（不加 ``VOICE_BRIDGE_`` 前缀，
  通过 pydantic ``validation_alias`` 显式绑定）

详见 docs/requirements/007-voice-call/design.md §4.2.1。
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    host: str = "127.0.0.1"
    port: int = 18900
    log_level: str = "INFO"

    public_url: str = ""
    """voice_bridge 对火山 RTC 云端可见的公网 URL 前缀（如 cloudflared URL）。

    生产前必须设置，否则火山 RTC 在 ``LLMConfig.Url`` 里拿到的就是 127.0.0.1，
    云端无法回调。开发期仅跑 mock 测试时可空。

    通过 ``VOICE_BRIDGE_PUBLIC_URL`` 环境变量覆盖。
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

    # ----- 通话默认参数 -----

    voice_type: str = "zh_female_linjianvhai_moon_bigtts"
    """火山 TTS 音色 id；spike 实测自然，沿用。"""

    welcome_message: str = "嗨，我们终于打上电话了，你最近怎么样？"
    """通话拨通后 AI 主动说的第一句话。"""

    default_persona: str = "default"
    """新建 session 时的默认 persona。"""

    default_model: str = ""
    """新建 session 时的默认 model；空字符串表示让 agent_bridge 自己决定。"""
