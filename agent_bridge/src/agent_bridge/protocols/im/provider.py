"""IMProvider Protocol(022 起):IM 平台 adapter 的统一契约。

加新 IM 平台 = 新增一个 ``adapters/<x>.py`` implements :class:`IMProvider`,
:class:`agent_bridge.protocols.im.IMRouter` / :class:`IMRuntime` /
:class:`OnboardSessionRegistry` / :class:`CredentialStore` /
:func:`register_routes` 全部零改动。

未来扩展(留好契约不实装,详见 requirement.md §3 / design.md §3.3):

- 飞书应用机器人 · 长连 Stream 模式
- Telegram Bot · getUpdates 长轮询
- NapCat / OneBot · 路线 B(灰色,当且仅当产品决策走 "agent 替用户社交" 形态)

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §3.3。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from .content import OutboundContent
from .event import InboundEvent

__all__ = ["IMProvider", "ProviderStatus"]


ProviderStatus = Literal["active", "degraded", "error", "stopped"]
"""IM 通道当前状态;对应 design §4 ``GET /v1/im/providers`` 返回的 ``status`` 字段:

- ``"active"``    — 长连建立,正常收发
- ``"degraded"``  — 短暂断连(等 SDK 内部 reconnect),不阻塞用户已有会话上下文
- ``"error"``     — 不可恢复错误(凭据失效 / fatal error),需要用户介入
- ``"stopped"``   — 主动 stop(进程退出 / 用户解绑前)
"""


class IMProvider(Protocol):
    """An IM platform adapter(e.g. QQ / 飞书 / Telegram)。

    生命周期:

    1. :meth:`start` 启动长连(adapter 内部可起独立 thread / async task);
       inbound 消息时调 ``on_inbound`` 回调
    2. :meth:`send` 回写消息给 IM 平台
    3. :meth:`stop` 停止长连,释放资源
    4. :meth:`status` 返回当前状态,供 ``GET /v1/im/providers`` 端点序列化

    **线程模型**:adapter 可以自起独立 thread(``qqbot-agent-sdk`` 的
    ``QQWebSocket`` 就是这么做的);``on_inbound`` 必须在 bridge 的主 event loop
    上被 schedule(用 ``asyncio.run_coroutine_threadsafe`` 或类似机制),
    因为它最终调到的 :class:`IMRouter.handle_inbound` 在主 loop 跑。
    """

    type: str
    """平台标识(``"qq"`` / ``"feishu"`` / ``"telegram"`` / ``"napcat"`` …)。"""

    bind_id: str
    """在 IM 平台内唯一标识当前绑定。QQ = ``user_openid``;飞书 = ``tenant_key``
    + ``user_id`` 拼接;Telegram = ``bot_id``。用于 IMRouter ``session_id_for()``
    计算 + actionbar 面板"已绑定列表"展示(脱敏后)+ ``IMRuntime._providers``
    dict 主键(``(type, bind_id)``)。"""

    def start(self, on_inbound: Callable[[InboundEvent], Awaitable[None]]) -> None:
        """启动长连。inbound 消息时调 ``on_inbound``。

        Args:
            on_inbound: async 回调,接 :class:`InboundEvent`,内部转发到
                :meth:`IMRouter.handle_inbound`。callback 必须 schedule 到
                bridge 主 event loop。
        """

    async def send(self, content: OutboundContent) -> None:
        """回写消息给 IM 平台。

        Args:
            content: agent 主链路跑完一轮的聚合输出。
        """

    async def stop(self) -> None:
        """停止长连,释放资源(关 WebSocket / cancel tasks / close HTTP client)。

        必须是幂等的:重复调用不应抛错。
        """

    def status(self) -> ProviderStatus:
        """返回当前通道状态。"""
