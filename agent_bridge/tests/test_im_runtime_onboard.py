"""IMRuntime + OnboardSessionRegistry 单测(M22.5)。

覆盖 design.md §3.4 / §3.8 + progress.md §M22.5 单测点:

IMRuntime:
- ``start()`` 加载 credentials.list_all() + 逐个 _spawn_provider
- ``stop()`` 并发停止所有 providers,timeout 守护
- ``register_after_onboard(cred)`` 落盘 + 立即建 provider
- ``unbind`` stop 对应 provider + delete cred + 返回找到/未找到
- ``list_status`` 脱敏 bind_id

OnboardSessionRegistry:
- ``start("qq")`` 创建 task,初始 state PENDING
- mock start_onboard:on_qr_ready 触发时 state 变 QR_READY + qr_url
- mock start_onboard 返回 result → state SUCCESS,IMRuntime.register_after_onboard 被调一次
- mock start_onboard 抛错 → state FAILED + error
- start("feishu") raise ValueError
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_bridge.protocols.im.credentials import CredentialStore, ImCredential
from agent_bridge.protocols.im.onboard import (
    OnboardSessionRegistry,
    OnboardStatus,
)
from agent_bridge.protocols.im.runtime import IMRuntime

# ---------- 测试 stub IMProvider(替代 QQAdapter,避免真启 SDK) ----------


@dataclass
class _StubProvider:
    """In-memory IMProvider for testing _spawn_provider / stop / etc.

    track started / stopped via internal flags;status() 从 _status 读。
    """

    type: str = "qq"
    bind_id: str = "OPENID-STUB"
    started: bool = False
    stopped: bool = False
    _status: str = "stopped"  # IMProvider.status() 返回

    def start(self, on_inbound: Any) -> None:
        self.started = True
        self._status = "active"

    async def send(self, content: Any) -> None:
        pass

    async def stop(self) -> None:
        self.stopped = True
        self._status = "stopped"

    def status(self) -> Any:
        return self._status


def _make_cred(bind_id: str = "OPENID-A") -> ImCredential:
    return ImCredential(
        im_type="qq",
        bind_id=bind_id,
        app_id="APP",
        client_secret="SEC",
        user_openid=bind_id,
    )


# ---------- IMRuntime fixtures ----------


@pytest.fixture
def credentials(tmp_path: Path) -> CredentialStore:
    return CredentialStore(base_dir=tmp_path / "im_credentials")


@pytest.fixture
def router() -> MagicMock:
    """mock IMRouter,handle_inbound async mock。"""
    r = MagicMock()
    r.handle_inbound = AsyncMock()
    return r


@pytest.fixture
def runtime(credentials: CredentialStore, router: MagicMock, tmp_path: Path) -> IMRuntime:
    return IMRuntime(
        router=router,
        credentials=credentials,
        resume_base_dir=tmp_path / "im_resume",
    )


# ---------- IMRuntime.start: 加载 + spawn ----------


@pytest.mark.asyncio
async def test_start_loads_credentials_and_spawns(
    runtime: IMRuntime, credentials: CredentialStore
) -> None:
    """``start()`` 从 credentials 读所有 cred,逐个 spawn provider。"""
    credentials.save(_make_cred("OPENID-A"))
    credentials.save(_make_cred("OPENID-B"))

    spawned: list[Any] = []

    def fake_build(cred: ImCredential) -> _StubProvider:
        p = _StubProvider(bind_id=cred.bind_id)
        spawned.append(p)
        return p

    with patch.object(runtime, "_build_provider", side_effect=fake_build):
        runtime.start()

    assert len(spawned) == 2
    assert all(p.started for p in spawned)
    assert set(p.bind_id for p in spawned) == {"OPENID-A", "OPENID-B"}


@pytest.mark.asyncio
async def test_start_one_provider_fail_does_not_break_others(
    runtime: IMRuntime, credentials: CredentialStore
) -> None:
    """单个 provider spawn 抛错 → log 后跳过,其他继续。"""
    credentials.save(_make_cred("OPENID-OK"))
    credentials.save(_make_cred("OPENID-BAD"))

    def fake_build(cred: ImCredential) -> _StubProvider:
        if cred.bind_id == "OPENID-BAD":
            raise RuntimeError("provider build failed")
        return _StubProvider(bind_id=cred.bind_id)

    with patch.object(runtime, "_build_provider", side_effect=fake_build):
        runtime.start()  # 不抛即通过

    statuses = runtime.list_status()
    assert len(statuses) == 1
    # OPENID-OK 是 9 字符 > 8,会被 _mask 脱敏(头 4 + ... + 尾 4)
    assert statuses[0].bind_id_masked == "OPEN...D-OK"


# ---------- IMRuntime.stop: 并发停止 ----------


@pytest.mark.asyncio
async def test_stop_calls_all_providers_stop(runtime: IMRuntime) -> None:
    p1 = _StubProvider(bind_id="OPENID-1")
    p2 = _StubProvider(bind_id="OPENID-2")
    runtime._providers[("qq", "OPENID-1")] = p1
    runtime._providers[("qq", "OPENID-2")] = p2

    await runtime.stop()
    assert p1.stopped
    assert p2.stopped
    assert runtime._providers == {}


@pytest.mark.asyncio
async def test_stop_empty_is_silent(runtime: IMRuntime) -> None:
    await runtime.stop()  # 无 provider,不抛


@pytest.mark.asyncio
async def test_stop_timeout_does_not_block(runtime: IMRuntime) -> None:
    """provider.stop 卡死时 timeout 守护,不阻塞 lifespan。"""

    class _SlowProvider(_StubProvider):
        async def stop(self) -> None:
            await asyncio.sleep(10.0)  # 远超 timeout

    runtime._providers[("qq", "OPENID-SLOW")] = _SlowProvider(bind_id="OPENID-SLOW")
    # timeout=0.05s 触发,gather 应该 200ms 内返回
    await asyncio.wait_for(runtime.stop(timeout=0.05), timeout=2.0)


# ---------- register_after_onboard ----------


@pytest.mark.asyncio
async def test_register_after_onboard_saves_and_spawns(
    runtime: IMRuntime, credentials: CredentialStore
) -> None:
    cred = _make_cred("OPENID-NEW")
    with patch.object(runtime, "_build_provider", return_value=_StubProvider(bind_id="OPENID-NEW")):
        runtime.register_after_onboard(cred)

    # cred 落盘
    loaded = credentials.list_all()
    assert len(loaded) == 1
    assert loaded[0].bind_id == "OPENID-NEW"
    # provider 已在 dict
    assert ("qq", "OPENID-NEW") in runtime._providers


@pytest.mark.asyncio
async def test_register_after_onboard_replaces_existing(
    runtime: IMRuntime,
) -> None:
    """同 (im_type, bind_id) 重新 onboard → 旧 provider 异步 stop,新的启动。"""
    old = _StubProvider(bind_id="OPENID-DUPE")
    runtime._providers[("qq", "OPENID-DUPE")] = old

    new = _StubProvider(bind_id="OPENID-DUPE")
    with patch.object(runtime, "_build_provider", return_value=new):
        runtime.register_after_onboard(_make_cred("OPENID-DUPE"))

    # 旧 provider 应该被 schedule stop(async task)
    await asyncio.sleep(0.01)  # 让 scheduled stop 跑一下
    assert old.stopped
    # 新 provider 已就位
    assert runtime._providers[("qq", "OPENID-DUPE")] is new


# ---------- unbind ----------


@pytest.mark.asyncio
async def test_unbind_stops_and_deletes(runtime: IMRuntime, credentials: CredentialStore) -> None:
    cred = _make_cred("OPENID-A")
    credentials.save(cred)
    p = _StubProvider(bind_id="OPENID-A")
    runtime._providers[("qq", "OPENID-A")] = p

    found = await runtime.unbind("qq", "OPENID-A")
    assert found is True
    assert p.stopped
    assert ("qq", "OPENID-A") not in runtime._providers
    assert credentials.list_all() == []


@pytest.mark.asyncio
async def test_unbind_nonexistent_returns_false(runtime: IMRuntime) -> None:
    found = await runtime.unbind("qq", "OPENID-NEVER")
    assert found is False


# ---------- list_status ----------


def test_list_status_masks_bind_id(runtime: IMRuntime) -> None:
    p = _StubProvider(bind_id="OPENID-VERY-LONG-IDENTIFIER")
    p._status = "active"
    runtime._providers[("qq", p.bind_id)] = p

    statuses = runtime.list_status()
    assert len(statuses) == 1
    assert statuses[0].im_type == "qq"
    assert statuses[0].status == "active"
    # 脱敏:头 4 + ... + 尾 4
    assert statuses[0].bind_id_masked == "OPEN...FIER"


# ---------- _build_provider 工厂行为 ----------


def test_build_provider_unsupported_im_type(runtime: IMRuntime) -> None:
    bad_cred = ImCredential(
        im_type="feishu",  # 本期不支持
        bind_id="X",
        app_id="A",
        client_secret="S",
    )
    with pytest.raises(ValueError, match="unsupported IM type"):
        runtime._build_provider(bad_cred)


# ============================================================================
# OnboardSessionRegistry tests
# ============================================================================


@pytest.fixture
def onboard_registry(runtime: IMRuntime) -> OnboardSessionRegistry:
    return OnboardSessionRegistry(im_runtime=runtime)


@pytest.fixture
def fake_onboard_result() -> Any:
    """模拟 qqbot_agent_sdk.OnboardResult。"""

    class _Result:
        app_id = "FAKE-APP-ID"
        client_secret = "FAKE-SECRET"
        user_openid = "FAKE-OPENID-12345678"

    return _Result()


# ---------- start: unsupported type ----------


@pytest.mark.asyncio
async def test_onboard_start_rejects_unsupported_im_type(
    onboard_registry: OnboardSessionRegistry,
) -> None:
    with pytest.raises(ValueError, match="unsupported IM type"):
        await onboard_registry.start("feishu")


# ---------- start: success flow ----------


@pytest.mark.asyncio
async def test_onboard_success_flow(
    onboard_registry: OnboardSessionRegistry,
    runtime: IMRuntime,
    fake_onboard_result: Any,
) -> None:
    """完整 happy path:start → PENDING → QR_READY(via on_qr)→ SUCCESS。"""

    async def fake_start_onboard(*, on_qr_ready: Any) -> Any:
        on_qr_ready("https://q.qq.com/qqbot/openclaw/connect.html?task_id=xxx")
        await asyncio.sleep(0)  # 让 QR_READY 状态被外部读到的机会
        return fake_onboard_result

    with (
        patch(
            "qqbot_agent_sdk.onboard.start_onboard",
            new=fake_start_onboard,
        ),
        patch.object(
            runtime, "_build_provider", return_value=_StubProvider(bind_id="FAKE-OPENID-12345678")
        ),
    ):
        task_id = await onboard_registry.start("qq")
        state = onboard_registry.get(task_id)
        assert state is not None
        assert state._task is not None

        # 等 background task 完成
        await state._task

    final = onboard_registry.get(task_id)
    assert final is not None
    assert final.status == OnboardStatus.SUCCESS
    assert final.qr_url is not None
    assert final.qr_url.startswith("https://q.qq.com/")
    assert final.bind_id_masked is not None
    assert final.error is None
    # IMRuntime 已经为新凭据 spawn provider
    assert ("qq", "FAKE-OPENID-12345678") in runtime._providers


# ---------- start: QR_READY 中间状态可见 ----------


@pytest.mark.asyncio
async def test_onboard_state_qr_ready_visible(
    onboard_registry: OnboardSessionRegistry,
    runtime: IMRuntime,
    fake_onboard_result: Any,
) -> None:
    """on_qr_ready 调用后,前端轮询能看到 QR_READY 状态(在 SUCCESS 之前)。"""

    qr_seen_event = asyncio.Event()
    sdk_resume_event = asyncio.Event()

    async def fake_start_onboard(*, on_qr_ready: Any) -> Any:
        on_qr_ready("https://q.qq.com/test")
        qr_seen_event.set()
        await sdk_resume_event.wait()  # 测试控制 SUCCESS 时机
        return fake_onboard_result

    with (
        patch("qqbot_agent_sdk.onboard.start_onboard", new=fake_start_onboard),
        patch.object(
            runtime, "_build_provider", return_value=_StubProvider(bind_id="FAKE-OPENID-12345678")
        ),
    ):
        task_id = await onboard_registry.start("qq")
        await qr_seen_event.wait()

        # 此时应 QR_READY,未 SUCCESS
        state = onboard_registry.get(task_id)
        assert state is not None
        assert state.status == OnboardStatus.QR_READY
        assert state.qr_url == "https://q.qq.com/test"

        sdk_resume_event.set()
        assert state._task is not None
        await state._task

    final_state = onboard_registry.get(task_id)
    assert final_state is not None
    assert final_state.status == OnboardStatus.SUCCESS


# ---------- start: failed ----------


@pytest.mark.asyncio
async def test_onboard_failure_marks_failed(
    onboard_registry: OnboardSessionRegistry,
) -> None:
    async def fake_start_onboard(*, on_qr_ready: Any) -> Any:
        raise RuntimeError("network down")

    with patch("qqbot_agent_sdk.onboard.start_onboard", new=fake_start_onboard):
        task_id = await onboard_registry.start("qq")
        state = onboard_registry.get(task_id)
        assert state is not None
        assert state._task is not None
        await state._task

    final = onboard_registry.get(task_id)
    assert final is not None
    assert final.status == OnboardStatus.FAILED
    assert final.error is not None
    assert "network down" in final.error


# ---------- get 未知 task_id ----------


def test_onboard_get_unknown_task_id_returns_none(
    onboard_registry: OnboardSessionRegistry,
) -> None:
    assert onboard_registry.get("never-existed-task") is None
