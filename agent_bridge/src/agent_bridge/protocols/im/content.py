"""IM 通道 outbound 内容 shape(022 起)。

agent 主链路跑完一轮后,IMRouter 把聚合的 assistant 文本封装成
:class:`OutboundContent`,交给具体 adapter 调平台 API 回写。

本期只支持**文本**。富媒体(图片 / 卡片 / 语音 / 文件)出 022 范围,留待未来扩展。

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §3.2。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["OutboundContent"]


@dataclass(frozen=True)
class OutboundContent:
    """IM adapter 回写给平台的一条消息。

    Attributes:
        chat_id: 回给谁。QQ c2c = ``user_openid``;群聊 = ``group_openid``;
            飞书 / Telegram 是各自的会话标识。
        chat_scope: 会话类型。本期 QQ c2c only,值为 ``"c2c"``。其他可选值
            (``"group"`` / ``"guild"`` / ``"dm"``)出 022 范围,但保留字段
            为未来扩展不破坏 shape。
        text: 消息正文(纯文本,不含 markdown / mentions / 富媒体)。
        reply_to_message_id: 引用回复的原 ``message_id``。QQ 官方 OpenAPI 的
            ``post_c2c_message`` 要求带上 ``msg_id`` 才能正确路由会话流;
            ``None`` 表示主动消息(非回复)。本期一律带 inbound 的 ``message_id``。
    """

    chat_id: str
    chat_scope: str
    text: str
    reply_to_message_id: str | None = None
