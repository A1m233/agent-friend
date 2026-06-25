"""``StartVoiceChat`` 请求 body 的组装。

把 spike `Custom.json` 的关键字段 inline 到 Python，每次拨打按运行时 settings
+ 通话上下文填充。**不**保留 JSON 文件读取——spike Custom.json 里有大量 spike
期硬编码（``Mode: ArkV3`` / ``EndPointId`` / 写死的 ``SystemMessages`` 等），
不适合作为产品代码。

详见 docs/requirements/007-voice-call/design.md §4.6。
"""

from __future__ import annotations

from typing import Any

from ..settings import VoiceBridgeSettings


def build_scenes(
    *,
    settings: VoiceBridgeSettings,
    call_id: str,
    room_id: str,
    bot_user_id: str,
    target_user_id: str,
    welcome_message: str | None = None,
) -> dict[str, Any]:
    """组装 ``StartVoiceChat`` 请求 body。

    关键字段：

    - ``LLMConfig.Mode = "CustomLLM"``：让火山把 LLM 请求打到 voice_bridge，
      而不是火山方舟（spike 用的 ``ArkV3``）
    - ``LLMConfig.Url``：voice_bridge 自身的公网 URL + ``/voice/llm/{call_id}/v1/chat/completions``
    - ``LLMConfig.SystemMessages = []``：空 system messages，让 agent 自己的
      :class:`agent.system_prompt.SystemPromptComposer` 完整接管 system prompt
      （含 voice 通道的 ChannelSection）
    - ``EnableConversationStateCallback = False``：本期不订阅状态回调，简化状态机

    Args:
        settings: voice_bridge settings。
        call_id: voice_bridge 自己签发的 uuid，火山的 ``TaskId`` 同值。
        room_id: 火山 RTC 房间 id。
        bot_user_id: AI 在房间里的 user id。
        target_user_id: 用户在房间里的 user id。
        welcome_message: 覆盖 settings 默认欢迎语；不传走 ``settings.welcome_message``。

    Returns:
        dict，可直接传给 :meth:`voice_bridge.rtc.openapi.VolcRtcClient.start_voice_chat`。

    Raises:
        ValueError: ``settings.public_url`` 为空（火山无法回调）。
    """
    if not settings.public_url:
        raise ValueError(
            "VOICE_BRIDGE_PUBLIC_URL 未配置；火山 RTC 无法回调到 voice_bridge LLM proxy"
        )

    welcome = welcome_message if welcome_message is not None else settings.welcome_message
    llm_url = settings.public_url.rstrip("/") + f"/voice/llm/{call_id}/v1/chat/completions"
    state_callback_url = settings.public_url.rstrip("/") + f"/voice/callbacks/state/{call_id}"
    subtitle_callback_url = settings.public_url.rstrip("/") + f"/voice/callbacks/subtitle/{call_id}"

    agent_config: dict[str, Any] = {
        "TargetUserId": [target_user_id],
        "UserId": bot_user_id,
        "EnableConversationStateCallback": True,
        "ServerMessageURLForRTS": state_callback_url,
        "ServerMessageSignatureForRTS": "agent-friend-smoke",
    }
    if welcome:
        agent_config["WelcomeMessage"] = welcome

    return {
        "AppId": settings.volc_rtc_app_id,
        "RoomId": room_id,
        "TaskId": call_id,
        "AgentConfig": agent_config,
        "Config": {
            "ASRConfig": {
                "Provider": "volcano",
                "ProviderParams": {
                    "Mode": "bigmodel",
                    "StreamMode": 0,
                    "AppId": settings.volc_speech_app_id,
                    "AccessToken": settings.volc_speech_access_token,
                },
                "VADConfig": {"SilenceTime": 300, "AIVAD": True},
            },
            "TTSConfig": {
                "Provider": "volcano",
                "ProviderParams": {
                    "app": {
                        "appid": settings.volc_speech_app_id,
                        "cluster": "volcano_tts",
                    },
                    "audio": {
                        "voice_type": settings.voice_type,
                        "speed_ratio": 1,
                        "pitch_ratio": 1,
                        "volume_ratio": 1,
                    },
                },
            },
            "LLMConfig": {
                "Mode": "CustomLLM",
                "Url": llm_url,
                "SystemMessages": [],
            },
            "SubtitleConfig": {
                "DisableRTSSubtitle": False,
                "ServerMessageUrl": subtitle_callback_url,
                "ServerMessageSignature": "agent-friend-smoke",
                "SubtitleMode": 0,
            },
            "InterruptMode": 0,
        },
    }
