"""voice_bridge 错误模型。

按"是否可恢复"分两组：

- **不可恢复错误**（认证失败 / 配置错 / agent_bridge 不可达 / 房间创建失败 /
  call_id 不存在 / 客户端请求格式错）→ 控制平面 HTTP 4xx/5xx + 用户语言 message
- **可恢复错误**（火山限流 / 网络瞬断）→ 在已接通的通话里 voice_bridge **不**
  拦截，让 agent 通过 fallback 拟人话术继续；只在重试到底仍失败时上抛

详见 docs/requirements/007-voice-call/design.md §4.10。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class _ErrorInfo:
    """错误的 HTTP / 用户语言映射。"""

    http_status: int
    user_message: str
    error_code: str


class VoiceBridgeError(Exception):
    """voice_bridge 自定义错误基类。"""

    info: ClassVar[_ErrorInfo]

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.info.user_message)
        self.detail = detail


class UnknownCallError(VoiceBridgeError):
    """``call_id`` 在注册表里找不到（surface 传错或通话已挂断）。"""

    info: ClassVar[_ErrorInfo] = _ErrorInfo(
        http_status=404,
        user_message="通话不存在或已结束",
        error_code="call_not_found",
    )


class InvalidRequestError(VoiceBridgeError):
    """客户端请求格式错（缺字段 / 字段值非法等）。"""

    info: ClassVar[_ErrorInfo] = _ErrorInfo(
        http_status=400,
        user_message="请求参数有误",
        error_code="invalid_request",
    )


class VolcAuthError(VoiceBridgeError):
    """火山引擎 AK/SK 错误或权限不足。**不可恢复**。"""

    info: ClassVar[_ErrorInfo] = _ErrorInfo(
        http_status=502,
        user_message="通话服务配置异常，请联系开发者",
        error_code="volc_auth_failed",
    )


class VolcRateLimitError(VoiceBridgeError):
    """火山 OpenAPI 限流。**可恢复**——控制平面层视为暂时性失败，建议稍后重试。"""

    info: ClassVar[_ErrorInfo] = _ErrorInfo(
        http_status=503,
        user_message="通话服务繁忙，请稍后再试",
        error_code="volc_rate_limited",
    )


class VolcUnreachableError(VoiceBridgeError):
    """无法连接到火山 OpenAPI（网络问题）。**可恢复**。"""

    info: ClassVar[_ErrorInfo] = _ErrorInfo(
        http_status=503,
        user_message="网络好像有点问题，请稍后再试",
        error_code="volc_unreachable",
    )


class VolcRoomCreateError(VoiceBridgeError):
    """火山 RTC 创建房间或拉起 AIGC 任务失败。"""

    info: ClassVar[_ErrorInfo] = _ErrorInfo(
        http_status=502,
        user_message="通话准备失败，请稍后再试",
        error_code="volc_room_create_failed",
    )


class AgentBridgeUnreachableError(VoiceBridgeError):
    """无法连接到 agent_bridge。**不可恢复**——通话无法绑 session。"""

    info: ClassVar[_ErrorInfo] = _ErrorInfo(
        http_status=502,
        user_message="AI 大脑暂时不可达，请稍后再试",
        error_code="agent_bridge_unreachable",
    )


class SessionBindFailedError(VoiceBridgeError):
    """绑定 session 失败（agent_bridge 返回 4xx/5xx，非 unreachable）。"""

    info: ClassVar[_ErrorInfo] = _ErrorInfo(
        http_status=502,
        user_message="通话准备失败，请稍后再试",
        error_code="session_bind_failed",
    )
