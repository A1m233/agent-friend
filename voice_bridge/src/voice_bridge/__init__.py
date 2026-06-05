"""agent-friend · voice-bridge

把火山 RTC AIGC 的"AI 语音通话"能力封装成 HTTP 控制平面 + LLM 入站代理，
让任意 surface（前端 / 桌宠 / IM）都能通过统一接口拨打/挂断与 agent 的语音通话。

详见 docs/requirements/007-voice-call/。
"""

from .app import create_app
from .settings import VoiceBridgeSettings

__version__ = "0.1.0"

__all__ = [
    "VoiceBridgeSettings",
    "create_app",
]
