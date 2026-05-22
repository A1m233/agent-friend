"""agent-friend · agent-bridge

把 :class:`agent.Conversation` 通过双协议（OpenAI ChatCompletion + AG-UI）的
HTTP SSE 网络服务对外暴露。本期（M6.1）只实现 OpenAI 出口；AG-UI 出口由
M6.2 落地。

详见 docs/requirements/006-agent-bridge/。
"""

from .app import create_app
from .settings import BridgeSettings

__version__ = "0.1.0"

__all__ = [
    "BridgeSettings",
    "create_app",
]
