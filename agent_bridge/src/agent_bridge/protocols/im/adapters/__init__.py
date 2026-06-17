"""IM adapter 集(022 起):每个 IM 平台一个 module。

本期(M22.4):

- :mod:`.qq` — :class:`QQAdapter` based on ``qqbot-agent-sdk``

未来扩展(留好抽象不实装,详见 requirement.md §3 / design.md §3.3):

- ``.feishu`` — 飞书应用机器人 · 长连 Stream 模式
- ``.telegram`` — Telegram Bot · getUpdates 长轮询
- ``.napcat`` — NapCat / OneBot · 路线 B(灰色,当且仅当产品决策走 "agent 替用户社交" 形态)

加新平台 = 新增一个 ``adapters/<x>.py`` implements :class:`agent_bridge.protocols.im.IMProvider`,
Router / Runtime / Onboard / Credentials / Routes 零改动。
"""
