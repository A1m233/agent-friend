"""IMRuntime inbound resolver and dedupe tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent.attachments import AttachmentRef
from agent_bridge.protocols.im.coalescing import QQAttachmentTextCoalescer
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.coordinator import IMBindingCoordinator
from agent_bridge.protocols.im.credentials import CredentialStore, ImCredential
from agent_bridge.protocols.im.dedupe import RecentMessageDeduper
from agent_bridge.protocols.im.event import InboundEvent, InboundInteraction
from agent_bridge.protocols.im.inbound_resolver import InboundResolveResult
from agent_bridge.protocols.im.provider import ProviderStatus
from agent_bridge.protocols.im.runtime import IMRuntime
from agent_bridge.protocols.im.state import ControlCapability, IMBindingKey, IMStateStore
from qqbot_agent_sdk.dto import MessageAttachment


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

    async def resolve(
        self,
        im_type: str,
        event: InboundEvent,
        *,
        provider_bind_id: str = "",
    ) -> InboundResolveResult:
        self.calls.append((im_type, event))
        return self.result


class AttachmentAwareResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, InboundEvent]] = []

    async def resolve(self, im_type: str, event: InboundEvent) -> InboundResolveResult:
        self.calls.append((im_type, event))
        if event.attachments:
            first = event.attachments[0]
            attachment = AttachmentRef(
                asset_id=f"asset-{event.message_id}",
                kind="image",
                media_type="image/png",
                display_name=first.filename or "attachment.png",
                size_bytes=10,
                sha256="0" * 64,
            )
            return InboundResolveResult.route(event, attachments=(attachment,))
        return InboundResolveResult.route(event)


class ControlledSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self.waiters: list[asyncio.Future[None]] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        waiter = asyncio.get_running_loop().create_future()
        self.waiters.append(waiter)
        await waiter

    async def wait_until_scheduled(self) -> None:
        await wait_until(lambda: bool(self.waiters))

    def release_next(self) -> None:
        waiter = self.waiters.pop(0)
        waiter.set_result(None)


async def wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


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
    resolver: StubResolver | AttachmentAwareResolver,
    deduper: RecentMessageDeduper | None = None,
    coordinator: IMBindingCoordinator | None = None,
    coalescer: QQAttachmentTextCoalescer | None = None,
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
        coordinator=coordinator,
        session_exists=lambda session_id: session_id in {"SESSION-BOUND", "SESSION-OTHER"},
        inbound_coalescer=coalescer,
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


def image_event(message_id: str, *, content: str = "", filename: str = "photo.png") -> InboundEvent:
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id="OPENID-USER",
        user_id="OPENID-USER",
        chat_scope="c2c",
        content=content,
        message_id=message_id,
        timestamp="2026-08-26T10:00:00Z",
        message_type=0,
        attachments=[
            MessageAttachment(
                url=f"https://cdn.example.test/{filename}",
                filename=filename,
                content_type="image/png",
            )
        ],
    )


def runtime_with_coalescer(
    tmp_path: Path,
    *,
    router: MagicMock,
    resolver: AttachmentAwareResolver,
    sleeper: ControlledSleep,
    deduper: RecentMessageDeduper | None = None,
) -> tuple[IMRuntime, QQAttachmentTextCoalescer, IMBindingCoordinator]:
    coordinator = IMBindingCoordinator()
    coalescer = QQAttachmentTextCoalescer(
        coordinator,
        window_seconds=3.0,
        sleep=sleeper,
    )
    runtime = make_runtime(
        tmp_path,
        router=router,
        resolver=resolver,
        deduper=deduper,
        coordinator=coordinator,
        coalescer=coalescer,
    )
    return runtime, coalescer, coordinator


@pytest.mark.asyncio
async def test_runtime_holds_attachment_until_timeout_then_routes_once(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = AttachmentAwareResolver()
    sleeper = ControlledSleep()
    runtime, coalescer, coordinator = runtime_with_coalescer(
        tmp_path,
        router=router,
        resolver=resolver,
        sleeper=sleeper,
    )
    provider = StubProvider()

    await runtime._make_inbound_cb(provider)(image_event("ATT-1"))

    assert resolver.calls == []
    router.handle_inbound.assert_not_awaited()
    assert coalescer.pending_count == 1
    assert coordinator.has_pending_turn(IMBindingKey("qq", "OPENID-STUB", "c2c", "OPENID-USER"))

    await sleeper.wait_until_scheduled()
    assert sleeper.delays == [3.0]
    sleeper.release_next()
    await wait_until(lambda: router.handle_inbound.await_count == 1)

    assert [event.message_id for _im_type, event in resolver.calls] == ["ATT-1"]
    assert router.handle_inbound.await_count == 1
    assert coalescer.pending_count == 0
    assert not coordinator.has_pending_turn(IMBindingKey("qq", "OPENID-STUB", "c2c", "OPENID-USER"))


@pytest.mark.asyncio
async def test_runtime_merges_first_text_with_pending_attachment(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = AttachmentAwareResolver()
    sleeper = ControlledSleep()
    runtime, coalescer, _coordinator = runtime_with_coalescer(
        tmp_path,
        router=router,
        resolver=resolver,
        sleeper=sleeper,
    )
    provider = StubProvider()
    callback = runtime._make_inbound_cb(provider)

    await callback(image_event("ATT-1", filename="context.png"))
    await callback(make_event(content="帮我分析这张图", message_id="TXT-1"))

    assert len(resolver.calls) == 1
    merged = resolver.calls[0][1]
    assert merged.message_id == "TXT-1"
    assert merged.content == "帮我分析这张图"
    assert [item.filename for item in merged.attachments] == ["context.png"]
    assert router.handle_inbound.await_count == 1
    routed = router.handle_inbound.await_args.args[1]
    assert routed is merged
    assert coalescer.pending_count == 0


@pytest.mark.asyncio
async def test_runtime_plain_text_still_routes_immediately(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = AttachmentAwareResolver()
    runtime, coalescer, _coordinator = runtime_with_coalescer(
        tmp_path,
        router=router,
        resolver=resolver,
        sleeper=ControlledSleep(),
    )

    await runtime._make_inbound_cb(StubProvider())(
        make_event(content="普通文字", message_id="TXT-1")
    )

    assert [event.message_id for _im_type, event in resolver.calls] == ["TXT-1"]
    router.handle_inbound.assert_awaited_once()
    assert coalescer.pending_count == 0


@pytest.mark.asyncio
async def test_runtime_second_attachment_flushes_first_without_aggregation(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = AttachmentAwareResolver()
    runtime, coalescer, _coordinator = runtime_with_coalescer(
        tmp_path,
        router=router,
        resolver=resolver,
        sleeper=ControlledSleep(),
    )
    callback = runtime._make_inbound_cb(StubProvider())

    await callback(image_event("ATT-1", filename="one.png"))
    await callback(image_event("ATT-2", filename="two.png"))

    assert [event.message_id for _im_type, event in resolver.calls] == ["ATT-1"]
    assert router.handle_inbound.await_count == 1
    assert coalescer.pending_count == 1

    await callback(make_event(content="第二张的配文", message_id="TXT-2"))

    assert [event.message_id for _im_type, event in resolver.calls] == ["ATT-1", "TXT-2"]
    second = resolver.calls[1][1]
    assert [item.filename for item in second.attachments] == ["two.png"]
    assert router.handle_inbound.await_count == 2
    assert coalescer.pending_count == 0


@pytest.mark.asyncio
async def test_runtime_text_after_timeout_is_a_separate_turn(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = AttachmentAwareResolver()
    sleeper = ControlledSleep()
    runtime, _coalescer, _coordinator = runtime_with_coalescer(
        tmp_path,
        router=router,
        resolver=resolver,
        sleeper=sleeper,
    )
    callback = runtime._make_inbound_cb(StubProvider())

    await callback(image_event("ATT-1"))
    await sleeper.wait_until_scheduled()
    sleeper.release_next()
    await wait_until(lambda: router.handle_inbound.await_count == 1)
    await callback(make_event(content="迟到的文字", message_id="TXT-LATE"))

    assert [event.message_id for _im_type, event in resolver.calls] == ["ATT-1", "TXT-LATE"]
    assert router.handle_inbound.await_count == 2


@pytest.mark.asyncio
async def test_runtime_dedupe_does_not_duplicate_or_renew_pending_attachment(
    tmp_path: Path,
) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = AttachmentAwareResolver()
    sleeper = ControlledSleep()
    runtime, coalescer, _coordinator = runtime_with_coalescer(
        tmp_path,
        router=router,
        resolver=resolver,
        sleeper=sleeper,
        deduper=RecentMessageDeduper(ttl_seconds=600, max_items=10),
    )
    callback = runtime._make_inbound_cb(StubProvider())
    inbound = image_event("ATT-DUPE")

    await callback(inbound)
    await callback(inbound)

    assert coalescer.pending_count == 1
    await sleeper.wait_until_scheduled()
    assert sleeper.delays == [3.0]
    sleeper.release_next()
    await wait_until(lambda: router.handle_inbound.await_count == 1)
    assert [event.message_id for _im_type, event in resolver.calls] == ["ATT-DUPE"]


@pytest.mark.asyncio
async def test_runtime_stop_cancels_pending_without_routing(tmp_path: Path) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = AttachmentAwareResolver()
    runtime, coalescer, coordinator = runtime_with_coalescer(
        tmp_path,
        router=router,
        resolver=resolver,
        sleeper=ControlledSleep(),
    )

    await runtime._make_inbound_cb(StubProvider())(image_event("ATT-STOP"))
    await runtime.stop()

    assert coalescer.pending_count == 0
    assert not coordinator.has_pending_turn(IMBindingKey("qq", "OPENID-STUB", "c2c", "OPENID-USER"))
    assert resolver.calls == []
    router.handle_inbound.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_stale_session_drops_expired_pending_instead_of_cross_routing(
    tmp_path: Path,
) -> None:
    router = MagicMock()
    router.handle_inbound = AsyncMock()
    resolver = AttachmentAwareResolver()
    sleeper = ControlledSleep()
    runtime, coalescer, _coordinator = runtime_with_coalescer(
        tmp_path,
        router=router,
        resolver=resolver,
        sleeper=sleeper,
    )
    binding_key = IMBindingKey("qq", "OPENID-STUB", "c2c", "OPENID-USER")

    await runtime._make_inbound_cb(StubProvider())(image_event("ATT-STALE"))
    runtime._state.set_binding(binding_key, "SESSION-OTHER")
    await sleeper.wait_until_scheduled()
    sleeper.release_next()
    await wait_until(lambda: coalescer.pending_count == 0)

    assert resolver.calls == []
    router.handle_inbound.assert_not_awaited()
