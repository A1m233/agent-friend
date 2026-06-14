"""``rtc/scenes.py`` 单元测试。"""

from __future__ import annotations

import pytest
from voice_bridge.rtc.scenes import build_scenes
from voice_bridge.settings import VoiceBridgeSettings


@pytest.fixture
def settings() -> VoiceBridgeSettings:
    return VoiceBridgeSettings(
        public_url="https://test.example.com",
        volc_rtc_app_id="rtc-app-id",
        volc_speech_app_id="speech-app-id",
        volc_speech_access_token="speech-token",
        voice_type="zh_female_test_bigtts",
        welcome_message="嗨呀",
    )


class TestBuildScenes:
    def test_required_top_level_fields(self, settings: VoiceBridgeSettings) -> None:
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        assert body["AppId"] == "rtc-app-id"
        assert body["RoomId"] == "room-1"
        assert body["TaskId"] == "call-1"

    def test_llm_url_points_to_voice_bridge_proxy(self, settings: VoiceBridgeSettings) -> None:
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        assert (
            body["Config"]["LLMConfig"]["Url"]
            == "https://test.example.com/voice/llm/call-1/v1/chat/completions"
        )
        assert body["Config"]["LLMConfig"]["Mode"] == "CustomLLM"

    def test_llm_system_messages_empty(self, settings: VoiceBridgeSettings) -> None:
        """LLMConfig.SystemMessages 必须是空列表——让 agent 自己的 system prompt 接管。"""
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        assert body["Config"]["LLMConfig"]["SystemMessages"] == []

    def test_callbacks_point_to_voice_bridge(self, settings: VoiceBridgeSettings) -> None:
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        assert body["AgentConfig"]["EnableConversationStateCallback"] is True
        assert (
            body["AgentConfig"]["ServerMessageURLForRTS"]
            == "https://test.example.com/voice/callbacks/state/call-1"
        )
        assert (
            body["Config"]["SubtitleConfig"]["ServerMessageUrl"]
            == "https://test.example.com/voice/callbacks/subtitle/call-1"
        )

    def test_welcome_message_default(self, settings: VoiceBridgeSettings) -> None:
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        assert body["AgentConfig"]["WelcomeMessage"] == "嗨呀"

    def test_welcome_message_override(self, settings: VoiceBridgeSettings) -> None:
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
            welcome_message="自定义欢迎",
        )
        assert body["AgentConfig"]["WelcomeMessage"] == "自定义欢迎"

    def test_target_user_in_list(self, settings: VoiceBridgeSettings) -> None:
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        assert body["AgentConfig"]["TargetUserId"] == ["user-1"]
        assert body["AgentConfig"]["UserId"] == "bot-1"

    def test_voice_type_propagated(self, settings: VoiceBridgeSettings) -> None:
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        assert (
            body["Config"]["TTSConfig"]["ProviderParams"]["audio"]["voice_type"]
            == "zh_female_test_bigtts"
        )

    def test_speech_credentials_used(self, settings: VoiceBridgeSettings) -> None:
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        asr = body["Config"]["ASRConfig"]["ProviderParams"]
        assert asr["AppId"] == "speech-app-id"
        assert asr["AccessToken"] == "speech-token"

    def test_missing_public_url_raises(self) -> None:
        settings = VoiceBridgeSettings(
            public_url="",
            volc_rtc_app_id="rtc-app-id",
            volc_speech_app_id="speech-app-id",
        )
        with pytest.raises(ValueError, match="VOICE_BRIDGE_PUBLIC_URL"):
            build_scenes(
                settings=settings,
                call_id="call-1",
                room_id="room-1",
                bot_user_id="bot-1",
                target_user_id="user-1",
            )

    def test_public_url_trailing_slash_normalized(self) -> None:
        settings = VoiceBridgeSettings(
            public_url="https://test.example.com/",
            volc_rtc_app_id="rtc-app-id",
            volc_speech_app_id="speech-app-id",
        )
        body = build_scenes(
            settings=settings,
            call_id="call-1",
            room_id="room-1",
            bot_user_id="bot-1",
            target_user_id="user-1",
        )
        assert (
            body["Config"]["LLMConfig"]["Url"]
            == "https://test.example.com/voice/llm/call-1/v1/chat/completions"
        )
