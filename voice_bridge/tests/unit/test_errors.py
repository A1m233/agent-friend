"""``errors.py`` 错误模型单元测试。"""

from __future__ import annotations

from voice_bridge.errors import (
    AgentBridgeUnreachableError,
    InvalidRequestError,
    SessionBindFailedError,
    UnknownCallError,
    VoiceBridgeError,
    VolcAuthError,
    VolcRateLimitError,
    VolcRoomCreateError,
    VolcUnreachableError,
)


class TestErrorInfo:
    def test_unknown_call_404(self) -> None:
        err = UnknownCallError()
        assert err.info.http_status == 404
        assert err.info.error_code == "call_not_found"
        assert "通话" in err.info.user_message
        assert isinstance(err, VoiceBridgeError)

    def test_invalid_request_400(self) -> None:
        assert InvalidRequestError().info.http_status == 400

    def test_volc_auth_502(self) -> None:
        assert VolcAuthError().info.http_status == 502
        assert "配置" in VolcAuthError().info.user_message

    def test_volc_rate_limit_503(self) -> None:
        assert VolcRateLimitError().info.http_status == 503

    def test_volc_unreachable_503(self) -> None:
        assert VolcUnreachableError().info.http_status == 503

    def test_volc_room_create_502(self) -> None:
        assert VolcRoomCreateError().info.http_status == 502

    def test_agent_bridge_unreachable_502(self) -> None:
        assert AgentBridgeUnreachableError().info.http_status == 502

    def test_session_bind_failed_502(self) -> None:
        assert SessionBindFailedError().info.http_status == 502

    def test_user_message_no_technical_internals(self) -> None:
        """所有错误的 user_message 都应该是用户语言，不含 Python / HTTP / 技术内部细节。"""
        for cls in (
            UnknownCallError,
            InvalidRequestError,
            VolcAuthError,
            VolcRateLimitError,
            VolcUnreachableError,
            VolcRoomCreateError,
            AgentBridgeUnreachableError,
            SessionBindFailedError,
        ):
            msg = cls().info.user_message
            for forbidden in ("Exception", "Traceback", "httpx", "401", "403", "secret", "AKLT"):
                assert forbidden not in msg, (
                    f"{cls.__name__}.user_message 暴露了技术内部: {forbidden!r}"
                )

    def test_detail_passthrough(self) -> None:
        err = UnknownCallError(detail="call abc 未找到")
        assert err.detail == "call abc 未找到"
        assert "call abc 未找到" in str(err)
