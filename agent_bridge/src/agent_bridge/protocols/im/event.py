"""IM 通道 inbound 事件 shape(022 起)。

直接 re-export ``qqbot-agent-sdk`` 的 :class:`InboundEvent` —— 该 SDK 设计时就把
inbound event 抽象成了 **platform-agnostic** 的 shape(``chat_id`` / ``user_id`` /
``chat_scope`` / ``content`` / ``message_id`` / ``timestamp`` / ``attachments`` /
``user_name``),跟 OpenClaw / Hermes 等同品类项目 channel adapter 的统一事件模型对齐。

未来若新增第二条 IM adapter(飞书 / Telegram / NapCat)且**不**基于 ``qqbot-agent-sdk``,
本文件改成 protocol-internal dataclass(同样字段集),所有现有 adapter 转译到这个
dataclass,Router / Runtime 零改动。本期不实装。

详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §3.3。
"""

from __future__ import annotations

from qqbot_agent_sdk.event_parser import InboundEvent

__all__ = ["InboundEvent"]
