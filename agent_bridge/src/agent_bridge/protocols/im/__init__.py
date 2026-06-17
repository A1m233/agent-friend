"""IM 通道接入(022 起):第三类 protocol。

跟 :mod:`agent_bridge.protocols.openai` / :mod:`agent_bridge.protocols.ag_ui` 并列,
但**差别**在于 IM 不是 HTTP inbound,而是 agent_bridge 主动出去连外部 IM gateway 的
长连出站模式。

模块布局:

- :mod:`.provider`        — :class:`IMProvider` Protocol(扩展点 · 加新平台 implements 它)
- :mod:`.event`           — :class:`InboundEvent`(借用 ``qqbot-agent-sdk`` 的 platform-agnostic shape)
- :mod:`.content`         — :class:`OutboundContent`(IM 回写的消息;本期文本)
- :mod:`.router`          — :class:`IMRouter`(inbound → ``session_id_for()`` → ``SessionBridge.bind_persistent`` → outbound)
- :mod:`.runtime`         — :class:`IMRuntime`(持有 providers + 生命周期,同 :class:`agent.runtime.AgentRuntime` 模式)
- :mod:`.onboard`         — :class:`OnboardSessionRegistry`(异步扫码 task 注册表)
- :mod:`.credentials`     — :class:`CredentialStore` / :class:`ImCredential`(AES-GCM 加密)
- :mod:`.routes`          — FastAPI ``/v1/im/*`` 路由
- :mod:`.adapters.qq`     — :class:`QQAdapter`(wrap ``qqbot-agent-sdk`` 4 件套)

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md。
"""

from .content import OutboundContent
from .credentials import CredentialStore, ImCredential
from .event import InboundEvent
from .onboard import OnboardSessionRegistry, OnboardStatus, OnboardTaskState
from .provider import IMProvider, ProviderStatus
from .router import IMRouter
from .routes import register_routes
from .runtime import IMRuntime, ProviderInfo

__all__ = [
    "CredentialStore",
    "IMProvider",
    "IMRouter",
    "IMRuntime",
    "ImCredential",
    "InboundEvent",
    "OnboardSessionRegistry",
    "OnboardStatus",
    "OnboardTaskState",
    "OutboundContent",
    "ProviderInfo",
    "ProviderStatus",
    "register_routes",
]
