"""Exercise frozen QQ approval routing and wire conversion without any network."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast


def run_qq_approval_smoke() -> dict[str, bool]:
    with TemporaryDirectory(prefix="af-qq-approval-smoke-") as temporary:
        return asyncio.run(_run(Path(temporary)))


async def _run(root: Path) -> dict[str, bool]:
    from agent.approvals import ApprovalBroker
    from agent.filesystem.models import ApprovalItem, PendingFilesystemApproval
    from agent.sessions.metadata import SessionMetadataStore
    from agent_bridge.protocols.im.adapters.qq import _keyboard_from_controls, _message_to_create
    from agent_bridge.protocols.im.approvals import IMApprovalService
    from agent_bridge.protocols.im.content import OutboundContent
    from agent_bridge.protocols.im.event import InboundEvent, InboundInteraction
    from agent_bridge.protocols.im.provider import IMProvider, OutboundSendResult
    from agent_bridge.protocols.im.state import IMBindingKey, IMStateStore

    class LocalTransport:
        type = "qq"
        bind_id = "smoke-user"

        def __init__(self) -> None:
            self.card = asyncio.Event()
            self.content: OutboundContent | None = None

        async def send(self, content: OutboundContent) -> OutboundSendResult:
            wire = _message_to_create(content, msg_seq=1).to_dict()
            if content.reply_to_event_id and wire.get("event_id") != content.reply_to_event_id:
                raise RuntimeError("QQ event reply field lost")
            if content.controls:
                keyboard = _keyboard_from_controls(content.controls)
                if keyboard is None or not keyboard.to_dict()["content"]["rows"]:
                    raise RuntimeError("QQ approval keyboard missing")
                self.content = content
                self.card.set()
            return OutboundSendResult("delivered", "fixture-receipt", controls="accepted")

    transport = LocalTransport()
    provider = cast(IMProvider, transport)
    state = IMStateStore(root / "im.json")
    state.set_binding(IMBindingKey("qq", "smoke-user", "c2c", "smoke-user"), "session")
    broker = ApprovalBroker(mode_store=SessionMetadataStore(root / "metadata.json"))
    service = IMApprovalService(
        broker,
        state,
        session_title=lambda _: "smoke",
        session_exists=lambda _: True,
    )
    service.activate(provider)
    event = InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_scope="c2c",
        chat_id="smoke-user",
        user_id="smoke-user",
        message_id="source",
        content="fixture",
        message_type=0,
        timestamp="2026-10-18T00:00:00+00:00",
    )
    pending = PendingFilesystemApproval.create(
        session_id="session",
        run_id="run",
        tool_call_id="call",
        reason="smoke",
        items=(ApprovalItem("file", root / "fixture.txt", "file", frozenset({"create"})),),
        actions=(),
    )
    with service.run_route(provider, event, "session", "run"):
        waiting = asyncio.create_task(
            asyncio.to_thread(broker.request, pending, timeout_seconds=3, surface="im:qq")
        )
        await asyncio.wait_for(transport.card.wait(), 2)
        content = transport.content
        if content is None or content.controls is None:
            raise RuntimeError("approval card absent")
        token = next(
            a.payload for row in content.controls.rows for a in row if a.label == "仅本次允许"
        )
        async with asyncio.timeout(2):
            while service._tokens.resolve(token) is None:
                await asyncio.sleep(0)
        click = InboundInteraction(
            "interaction",
            "INTERACTION_CREATE",
            "c2c",
            "smoke-user",
            "smoke-user",
            token,
            reply_event_id="dispatch-event",
        )
        await service.handle(provider, click)
        decision = await waiting
        if decision is None or not decision.approved:
            raise RuntimeError("QQ approval did not unblock request")
        version = pending.version
        await service.handle(provider, click)
        if pending.version != version:
            raise RuntimeError("QQ callback replay changed approval")
    if broker.surface_available("session", "run", "im:qq"):
        raise RuntimeError("QQ run registration leaked")
    service.deactivate(provider)
    return {"qq_approval_roundtrip": True, "stale_callback_rejected": True, "run_cleanup": True}
