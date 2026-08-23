"""IMRuntime inbound resolver and dedupe tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.credentials import CredentialStore, ImCredential
from agent_bridge.protocols.im.dedupe import RecentMessageDeduper
from agent_bridge.protocols.im.event import InboundEvent, InboundInteraction
from agent_bridge.protocols.im.inbound_resolver import InboundResolveResult
from agent_bridge.protocols.im.provider import ProviderStatus
from agent_bridge.protocols.im.runtime import IMRuntime
from agent_bridge.protocols.im.state import ControlCapability, IMBindingKey, IMStateStore


class StubProvider:
    type = "qq"
    bind_id = "OPENID-STUB"

    def __init__(self) -> None:
        self.sent: list[OutboundContent] = []

    def start(
        self,
        on_inbound: Callable[[InboundEvent], Awaitable[None]],
        on_interaction: Callable[[InboundInteraction], Awaitable[None]] | None = None,
    ) -> None:
        pass

    async def send(self, content: OutboundContent) -> None:
        self.sent.append(content)

    async def stop(self) -> None:
        pass

    def status(self) -> ProviderStatus:
        return "active"

    def controls_capability(self) -> ControlCapability:
        return "unknown"


class StubResolver:
    def __init__(self, result: InboundResolveResult) -> None:
        self.result = result
        self.calls: list[tuple[str, InboundEvent]] = []

    async def resolve(self, im_type: str, event: InboundEvent) -> InboundResolveResult:
        self.calls.append((im_type, event))
        return self.result


def make_event(*, content: str = "hello", message_id: str = "MSG-1") -> InboundEvent:
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id="OPENID-USER",
        user_id="OPENID-USER",
        chat_scope="c2c",
        content=content,
        message_id=message_id,
        timestamp="2026-06-29T10:00:00Z",
        message_type=0,
    )


def make_runtime(
    tmp_path: Path,
    *,
    router: MagicMock,
    resolver: StubResolver,
    deduper: RecentMessageDeduper | None = None,
) -> IMRuntime:
    state = IMStateStore(tmp_path / "im_state.json")
    state.set_binding(
        IMBindingKey("qq", "OPENID-STUB", "c2c", "OPENID-USER"),
        "SESSION-BOUND",
    )
    return IMRuntime(
        router=router,
        credentials=CredentialStore(base_dir=tmp_path / "im_credentials"),
        resume_base_dir=tmp_path / "im_resume",
        inbound_resolver=resolver,
        deduper=deduper,
        state_store=state,
        session_exists=lambda session_id: session_id == "SESSION-BOUND",
    )


@pytest.mark.asyncio
async def test_runtime_routes_resolved_event(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    event = make_event(content="before")
    resolved = replace(event, content="after")
    resolver = StubResolver(InboundResolveResult.route(resolved))
    runtime = make_runtime(tmp_path, router=router, resolver=resolver)
    provider = StubProvider()

    await runtime._make_inbound_cb(provider)(event)

    router.handle_inbound.assert_awaited_once()
    assert router.handle_inbound.await_args.args[0] == "qq"
    assert router.handle_inbound.await_args.args[1].content == "after"
    assert router.handle_inbound.await_args.kwargs["provider_or_send"] is provider
    assert router.handle_inbound.await_args.kwargs["session_id"] == "SESSION-BOUND"
    assert provider.sent == []
    snapshot = runtime.presence_snapshots()[0]
    assert snapshot.last_interaction_at is not None
    assert snapshot.last_interaction_at.isoformat() == "2026-06-29T10:00:00+00:00"
    assert snapshot.binding_updated_at is not None


@pytest.mark.asyncio
async def test_runtime_ignores_empty_resolved_event(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    event = make_event(content="")
    resolved = replace(event, content="  ")
    resolver = StubResolver(InboundResolveResult.route(resolved))
    runtime = make_runtime(tmp_path, router=router, resolver=resolver)
    provider = StubProvider()

    await runtime._make_inbound_cb(provider)(event)

    router.handle_inbound.assert_not_awaited()
    assert provider.sent == []


@pytest.mark.asyncio
async def test_runtime_direct_reply_skips_router(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    reply = OutboundContent(
        chat_id="OPENID-USER",
        chat_scope="c2c",
        text="没听清",
        reply_to_message_id="MSG-1",
    )
    resolver = StubResolver(InboundResolveResult.direct_reply(reply, reason="voice_failed"))
    runtime = make_runtime(tmp_path, router=router, resolver=resolver)
    provider = StubProvider()

    await runtime._make_inbound_cb(provider)(make_event())

    router.handle_inbound.assert_not_awaited()
    assert provider.sent == [reply]


@pytest.mark.asyncio
async def test_runtime_dedupes_before_resolver_and_router(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = StubResolver(InboundResolveResult.route(make_event()))
    runtime = make_runtime(
        tmp_path,
        router=router,
        resolver=resolver,
        deduper=RecentMessageDeduper(ttl_seconds=600, max_items=10),
    )
    provider = StubProvider()
    event = make_event(message_id="MSG-DUPE")
    cb = runtime._make_inbound_cb(provider)

    await cb(event)
    await cb(event)

    assert len(resolver.calls) == 1
    router.handle_inbound.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_releases_provider_lock_on_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = StubResolver(InboundResolveResult.route(make_event()))
    runtime = make_runtime(tmp_path, router=router, resolver=resolver)
    provider = StubProvider()
    fake_lock = MagicMock()

    monkeypatch.setattr(runtime, "_build_provider", lambda _cred: provider)
    monkeypatch.setattr(runtime, "_acquire_provider_lock", lambda _im_type, _bind_id: fake_lock)

    runtime._spawn_provider(
        ImCredential(
            im_type="qq",
            bind_id="OPENID-STUB",
            app_id="APPID",
            client_secret="SECRET",
            user_openid="OPENID-STUB",
        )
    )
    await runtime.stop()

    fake_lock.release.assert_called_once()
