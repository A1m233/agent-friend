"""QQAdapter 单测(M22.4)。

覆盖 design.md §3.5 + progress.md §M22.4 单测点。**不接真 QQ gateway**,所有 SDK
调用 mock 掉。

测试点:

- mock QQWebSocket + QQApiClient,验证 start 调用顺序(setup → get_gateway_url_sync
  → ws.start)
- WSCallbacks.on_connected → status active;on_disconnected → degraded;
  on_fatal_error → error
- _on_message 调用 EventParser.parse → 仅 chat_scope=="c2c" 转发(group/guild 不转发)
- send 错误码非零(43xxx / 11xxx)仅 log warning 不抛
- send 网络异常 catch + log exception 不抛
- _load_resume_token 空文件 → (None, None);有效文件 → (session_id, last_seq)
- _save_resume_token 写出后 _load_resume_token 能读回
- stop 幂等性
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_bridge.protocols.im.adapters.qq import QQAdapter
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.credentials import ImCredential
from agent_bridge.protocols.im.event import InboundEvent

# ---------- fixtures ----------


@pytest.fixture
def cred() -> ImCredential:
    return ImCredential(
        im_type="qq",
        bind_id="OPENID-USER",
        app_id="APPID-1",
        client_secret="SECRET-X",
        user_openid="OPENID-USER",
    )


@pytest.fixture
def resume_dir(tmp_path: Path) -> Path:
    return tmp_path / "im_resume"


@pytest.fixture
def adapter(cred: ImCredential, resume_dir: Path) -> QQAdapter:
    return QQAdapter(cred=cred, resume_base_dir=resume_dir)


def _make_c2c_event(chat_id: str = "OPENID-X", content: str = "你好") -> InboundEvent:
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id=chat_id,
        user_id=chat_id,
        chat_scope="c2c",
        content=content,
        message_id="MSG-1",
        timestamp="2026-06-17T10:00:00Z",
        message_type=0,
    )


# ---------- 初始 status ----------


def test_initial_status_stopped(adapter: QQAdapter) -> None:
    assert adapter.status() == "stopped"


def test_adapter_type_is_qq(adapter: QQAdapter) -> None:
    assert adapter.type == "qq"


def test_bind_id_from_user_openid(cred: ImCredential, resume_dir: Path) -> None:
    a = QQAdapter(cred=cred, resume_base_dir=resume_dir)
    assert a.bind_id == "OPENID-USER"


# ---------- start 调用顺序 ----------


@pytest.mark.asyncio
async def test_start_setup_and_gateway_then_ws_start(adapter: QQAdapter, resume_dir: Path) -> None:
    """start 调用顺序:QQApiClient(创建)→ setup(httpx)→ get_gateway_url_sync →
    QQWebSocket(创建)→ ws.start。"""
    mock_api = MagicMock()
    mock_api.get_gateway_url_sync.return_value = "wss://api.sgroup.qq.com/websocket"
    mock_ws = MagicMock()

    with (
        patch(
            "agent_bridge.protocols.im.adapters.qq.QQApiClient", return_value=mock_api
        ) as api_cls,
        patch("agent_bridge.protocols.im.adapters.qq.QQWebSocket", return_value=mock_ws) as ws_cls,
        patch("agent_bridge.protocols.im.adapters.qq.httpx.AsyncClient") as http_cls,
    ):
        mock_http = MagicMock()
        http_cls.return_value = mock_http
        adapter.start(on_inbound=AsyncMock())

    # QQApiClient 用 cred 构造
    api_cls.assert_called_once()
    assert api_cls.call_args.kwargs["app_id"] == "APPID-1"
    assert api_cls.call_args.kwargs["client_secret"] == "SECRET-X"
    # setup 收到 httpx client
    mock_api.setup.assert_called_once_with(mock_http)
    # gateway url 拿到
    mock_api.get_gateway_url_sync.assert_called_once()
    # ws 用 callbacks 构造
    ws_cls.assert_called_once()
    # ws.start 拿到 gateway url + main loop
    mock_ws.start.assert_called_once()
    args = mock_ws.start.call_args.args
    assert args[0] == "wss://api.sgroup.qq.com/websocket"


# ---------- status 由 WSCallbacks 驱动 ----------


@pytest.mark.asyncio
async def test_on_connected_sets_status_active(adapter: QQAdapter) -> None:
    captured: dict[str, Any] = {}

    def grab_callbacks(*_args: Any, **kwargs: Any) -> MagicMock:
        captured["callbacks"] = kwargs.get("callbacks")
        return MagicMock()

    with (
        patch(
            "agent_bridge.protocols.im.adapters.qq.QQWebSocket",
            side_effect=grab_callbacks,
        ),
        patch("agent_bridge.protocols.im.adapters.qq.QQApiClient"),
        patch("agent_bridge.protocols.im.adapters.qq.httpx.AsyncClient"),
    ):
        adapter.start(on_inbound=AsyncMock())

    cbs = captured["callbacks"]
    cbs.on_connected()
    assert adapter.status() == "active"
    cbs.on_disconnected()
    assert adapter.status() == "degraded"
    cbs.on_fatal_error("4914", "Bot is locked")
    assert adapter.status() == "error"


# ---------- _on_message chat_scope filter ----------


@pytest.mark.asyncio
async def test_on_message_c2c_forwarded(adapter: QQAdapter) -> None:
    on_inbound = AsyncMock()
    adapter._on_inbound = on_inbound  # bypass start

    fake_event = _make_c2c_event()
    with patch.object(adapter._parser, "parse", return_value=fake_event):
        await adapter._on_message("C2C_MESSAGE_CREATE", {})
    on_inbound.assert_awaited_once_with(fake_event)


@pytest.mark.asyncio
async def test_on_message_group_ignored(adapter: QQAdapter) -> None:
    on_inbound = AsyncMock()
    adapter._on_inbound = on_inbound

    group_event = InboundEvent(
        event_type="GROUP_AT_MESSAGE_CREATE",
        chat_id="GROUP-X",
        user_id="USER-X",
        chat_scope="group",
        content="@bot hi",
        message_id="MSG-G",
        timestamp="2026-06-17T10:00:00Z",
        message_type=0,
    )
    with patch.object(adapter._parser, "parse", return_value=group_event):
        await adapter._on_message("GROUP_AT_MESSAGE_CREATE", {})
    on_inbound.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_unparseable_ignored(adapter: QQAdapter) -> None:
    on_inbound = AsyncMock()
    adapter._on_inbound = on_inbound
    with patch.object(adapter._parser, "parse", return_value=None):
        await adapter._on_message("UNKNOWN_EVENT", {})
    on_inbound.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_before_start_warns_but_no_raise(adapter: QQAdapter) -> None:
    """start 前收到消息(理论不该发生)→ log warning,不抛。"""
    # _on_inbound 未设置(None);直接调 _on_message
    with patch.object(adapter._parser, "parse", return_value=_make_c2c_event()):
        await adapter._on_message("C2C_MESSAGE_CREATE", {})  # 不抛即通过


# ---------- send ----------


@pytest.mark.asyncio
async def test_send_calls_post_c2c_with_message_to_create(
    adapter: QQAdapter,
) -> None:
    mock_api = MagicMock()
    mock_api.post_c2c_message = AsyncMock(return_value={"code": 0})
    adapter._api = mock_api

    content = OutboundContent(
        chat_id="OPENID-DEST",
        chat_scope="c2c",
        text="hello",
        reply_to_message_id="MSG-IN-123",
    )
    await adapter.send(content)

    mock_api.post_c2c_message.assert_awaited_once()
    chat_id_arg = mock_api.post_c2c_message.await_args.args[0]
    msg_arg = mock_api.post_c2c_message.await_args.args[1]
    assert chat_id_arg == "OPENID-DEST"
    assert msg_arg.content == "hello"
    assert msg_arg.msg_type == 0  # 文本
    assert msg_arg.msg_id == "MSG-IN-123"


@pytest.mark.asyncio
async def test_send_nonzero_code_logs_warning_no_raise(
    adapter: QQAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    """43xxx / 11xxx 非零 code 不抛。"""
    mock_api = MagicMock()
    mock_api.post_c2c_message = AsyncMock(return_value={"code": 40034, "message": "内容审核未通过"})
    adapter._api = mock_api

    with caplog.at_level("WARNING"):
        await adapter.send(
            OutboundContent(
                chat_id="X",
                chat_scope="c2c",
                text="敏感",
                reply_to_message_id="MSG",
            )
        )
    assert "40034" in caplog.text
    assert "内容审核未通过" in caplog.text


@pytest.mark.asyncio
async def test_send_network_exception_caught(
    adapter: QQAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    """网络/HTTP 异常 catch + log,不抛。"""
    mock_api = MagicMock()
    mock_api.post_c2c_message = AsyncMock(side_effect=ConnectionError("DNS failed"))
    adapter._api = mock_api

    with caplog.at_level("ERROR"):
        await adapter.send(OutboundContent(chat_id="X", chat_scope="c2c", text="hi"))
    assert "网络" in caplog.text or "HTTP" in caplog.text or "DNS" in caplog.text


@pytest.mark.asyncio
async def test_send_before_start_warns_no_raise(adapter: QQAdapter) -> None:
    """start 前 send → log warning,不抛。"""
    # _api is None
    await adapter.send(OutboundContent(chat_id="X", chat_scope="c2c", text="hi"))  # 不抛即通过


# ---------- resume token round-trip ----------


def test_resume_token_load_when_missing_returns_none_pair(
    adapter: QQAdapter,
) -> None:
    assert adapter._load_resume_token() == (None, None)


def test_resume_token_save_then_load_round_trip(adapter: QQAdapter) -> None:
    adapter._save_resume_token("SESS-X", 123)
    assert adapter._load_resume_token() == ("SESS-X", 123)


def test_resume_token_overwrites_on_save(adapter: QQAdapter) -> None:
    adapter._save_resume_token("OLD", 1)
    adapter._save_resume_token("NEW", 999)
    assert adapter._load_resume_token() == ("NEW", 999)


def test_resume_token_corrupt_returns_none_pair(adapter: QQAdapter) -> None:
    """坏文件 → (None, None),SDK 走全新 IDENTIFY。"""
    adapter._resume_path.parent.mkdir(parents=True, exist_ok=True)
    adapter._resume_path.write_text("not-json")
    assert adapter._load_resume_token() == (None, None)


def test_resume_token_path_is_hash_prefixed(cred: ImCredential, tmp_path: Path) -> None:
    """文件名 = ``qq_<sha256(bind_id)[:16]>.json``,稳定可定位。"""
    import hashlib

    base = tmp_path / "im_resume"
    a = QQAdapter(cred=cred, resume_base_dir=base)
    expected_hash = hashlib.sha256(b"OPENID-USER").hexdigest()[:16]
    assert a._resume_path == base / f"qq_{expected_hash}.json"


# ---------- stop 幂等 ----------


@pytest.mark.asyncio
async def test_stop_idempotent(adapter: QQAdapter) -> None:
    """重复 stop 不抛。"""
    await adapter.stop()  # 从未 start;不抛
    await adapter.stop()  # 再调一次;不抛
    assert adapter.status() == "stopped"


@pytest.mark.asyncio
async def test_stop_closes_ws_and_http_client(adapter: QQAdapter) -> None:
    """stop 时调 ws.async_stop + http_client.aclose。"""
    mock_ws = MagicMock()
    mock_ws.async_stop = AsyncMock()
    mock_http = MagicMock()
    mock_http.aclose = AsyncMock()
    adapter._ws = mock_ws
    adapter._http_client = mock_http
    adapter._api = MagicMock()

    await adapter.stop()
    mock_ws.async_stop.assert_awaited_once()
    mock_http.aclose.assert_awaited_once()
    assert adapter.status() == "stopped"
    assert adapter._api is None
