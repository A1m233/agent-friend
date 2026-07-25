"""IM 通道 outbound 内容 shape(022 起)。

agent 主链路跑完一轮后,IMRouter 把聚合的 assistant 文本封装成
:class:`OutboundContent`,交给具体 adapter 调平台 API 回写。

本期只支持**文本正文**。正文可以声明为纯文本或 Markdown;富媒体
(图片 / 卡片 / 语音 / 文件)出 022 范围,留待未来扩展。

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §3.2。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "OutboundAction",
    "OutboundContent",
    "OutboundControls",
    "OutboundTextFormat",
]


OutboundTextFormat = Literal["plain_text", "markdown"]
"""IM outbound 文本正文的格式声明。

``plain_text`` 表示 adapter 必须按纯文本发送;``markdown`` 表示 adapter 可在平台
支持时使用 Markdown 消息类型,不支持的平台再自行降级。
"""


@dataclass(frozen=True)
class OutboundAction:
    """A platform-neutral clickable action attached to an outbound message."""

    id: str
    label: str
    visited_label: str
    payload: str
    style: Literal["default", "primary"] = "default"
    click_limit: int = 1


@dataclass(frozen=True)
class OutboundControls:
    """Rows of actions; adapters translate them to platform-specific controls."""

    rows: tuple[tuple[OutboundAction, ...], ...]


@dataclass(frozen=True)
class OutboundContent:
    """IM adapter 回写给平台的一条消息。

    Attributes:
        chat_id: 回给谁。QQ c2c = ``user_openid``;群聊 = ``group_openid``;
            飞书 / Telegram 是各自的会话标识。
        chat_scope: 会话类型。本期 QQ c2c only,值为 ``"c2c"``。其他可选值
            (``"group"`` / ``"guild"`` / ``"dm"``)出 022 范围,但保留字段
            为未来扩展不破坏 shape。
        text: 消息正文。
        text_format: 消息正文格式。默认 ``"plain_text"`` 保持既有 direct reply /
            fallback 语义;agent 主链路的 assistant 正常回复可显式声明
            ``"markdown"``。
        reply_to_message_id: 引用回复的原 ``message_id``。QQ 官方 OpenAPI 的
            ``post_c2c_message`` 要求带上 ``msg_id`` 才能正确路由会话流;
            ``None`` 表示主动消息(非回复)。本期一律带 inbound 的 ``message_id``。
    """

    chat_id: str
    chat_scope: str
    text: str
    reply_to_message_id: str | None = None
    text_format: OutboundTextFormat = "plain_text"
    controls: OutboundControls | None = None
