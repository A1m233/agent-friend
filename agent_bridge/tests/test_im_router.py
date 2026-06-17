"""IMRouter 单测(M22.3)。

覆盖 design.md §3.2 + progress.md §M22.3 单测点:

- ``session_id_for`` 稳定输出 + 同 chat_id 同结果
- ``handle_inbound`` 调用 ``session_bridge.bind_persistent`` 时 ``thread_id`` ==
  ``session_id_for(...)``
- mock ``Conversation.stream`` 返回 ``[TextDelta("hello"), TextDelta(" world"),
  TurnDone()]`` → ``send_fn`` 收到 ``OutboundContent.text == "hello world"``
- mock ``Conversation.stream`` 抛错 → ``send_fn`` 收到
  ``OutboundContent.text == FALLBACK_TEXT``
- ``ToolCallRequest`` / ``ToolCallResult`` 不污染 outbound(只 TextDelta 进 buffer)
- ``send_fn`` 收到的 ``chat_id`` / ``chat_scope`` / ``reply_to_message_id`` 跟
  inbound event 一致
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.event import InboundEvent
from agent_bridge.protocols.im.router import FALLBACK_TEXT, IMRouter

from agent import TextDelta, ToolCallRequest, ToolCallResult, TurnDone

# ---------- fixtures ----------


@pytest.fixture
def session_bridge() -> MagicMock:
    """mock SessionBridge;`bind_persistent` 返回 mock Conversation。"""
    return MagicMock()


@pytest.fixture
def router(session_bridge: MagicMock) -> IMRouter:
    return IMRouter(
        session_bridge=session_bridge,
        default_persona="default-persona",
        default_model="default-model",
    )


def _make_event(
    *,
    chat_id: str = "OPENID-USER-A",
    content: str = "你好",
    message_id: str = "MSG-001",
    chat_scope: str = "c2c",
    user_id: str | None = None,
) -> InboundEvent:
    """构造 InboundEvent test fixture(qqbot_agent_sdk dataclass)。"""
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id=chat_id,
        user_id=user_id or chat_id,
        chat_scope=chat_scope,
        content=content,
        message_id=message_id,
        timestamp="2026-06-17T10:00:00Z",
        message_type=0,
    )


def _make_conv(events: list[Any] | Exception) -> MagicMock:
    """构造 mock Conversation:``stream(user_input)`` 返回 generator(或抛 exc)。"""
    conv = MagicMock()
    if isinstance(events, Exception):

        def _raise(_user_input: str) -> Any:
            raise events

        conv.stream.side_effect = _raise
    else:
        conv.stream.return_value = iter(events)
    return conv


# ---------- session_id_for ----------


def test_session_id_for_stable_output(router: IMRouter) -> None:
    """同 (im_type, chat_id) 同结果。"""
    ev = _make_event(chat_id="OPENID-A")
    assert router.session_id_for("qq", ev) == "im:qq:OPENID-A"
    # 同入参再算一次还应一样
    assert router.session_id_for("qq", ev) == "im:qq:OPENID-A"


def test_session_id_for_different_chat_id_different(router: IMRouter) -> None:
    a = _make_event(chat_id="OPENID-A")
    b = _make_event(chat_id="OPENID-B")
    assert router.session_id_for("qq", a) != router.session_id_for("qq", b)


def test_session_id_for_different_im_type_different(router: IMRouter) -> None:
    ev = _make_event(chat_id="OPENID-X")
    assert router.session_id_for("qq", ev) != router.session_id_for("feishu", ev)


# ---------- handle_inbound 调用 bind_persistent ----------


@pytest.mark.asyncio
async def test_handle_inbound_passes_session_id_to_bind_persistent(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    """``thread_id`` == ``session_id_for(im_type, event)``。"""
    session_bridge.bind_persistent.return_value = _make_conv([TurnDone()])
    ev = _make_event(chat_id="OPENID-XYZ", content="hi")

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", ev, send)

    session_bridge.bind_persistent.assert_called_once()
    boot = session_bridge.bind_persistent.call_args.args[0]
    assert boot.thread_id == "im:qq:OPENID-XYZ"
    assert boot.new_user_input == "hi"
    assert boot.default_persona == "default-persona"
    assert boot.default_model == "default-model"


# ---------- 文本聚合 ----------


@pytest.mark.asyncio
async def test_text_delta_aggregation(router: IMRouter, session_bridge: MagicMock) -> None:
    """``[TextDelta("hello"), TextDelta(" world"), TurnDone()]`` → "hello world"。"""
    session_bridge.bind_persistent.return_value = _make_conv(
        [TextDelta(text="hello"), TextDelta(text=" world"), TurnDone()]
    )

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", _make_event(), send)

    assert len(captured) == 1
    assert captured[0].text == "hello world"


@pytest.mark.asyncio
async def test_text_aggregated_until_generator_exhausts(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    """不在 TurnDone 处 break,让 generator 自然耗尽。

    设计约定:跟 ag_ui/openai encoder 同款,for ev in stream 不 break — 否则
    Conversation.stream 的 finally 会把 GeneratorExit 误判为业务中断,落一份
    partial=True 的 assistant_event,LLM 下一轮自洽地把回复说两遍(issue 由
    022 真跑发现并已修)。
    """
    exhausted = False
    closed_early = False

    def stream(_user_input: str) -> Any:
        nonlocal exhausted, closed_early
        try:
            yield TextDelta(text="part1 ")
            yield TextDelta(text="part2")
            yield TurnDone()
        except GeneratorExit:
            closed_early = True
            raise
        else:
            exhausted = True

    conv = MagicMock()
    conv.stream.side_effect = stream
    session_bridge.bind_persistent.return_value = conv

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", _make_event(), send)
    assert captured[0].text == "part1 part2"
    assert exhausted is True
    assert closed_early is False


# ---------- 异常兜底 ----------


@pytest.mark.asyncio
async def test_stream_exception_falls_back(router: IMRouter, session_bridge: MagicMock) -> None:
    """``Conversation.stream`` 抛错 → 回 ``FALLBACK_TEXT``。"""
    session_bridge.bind_persistent.return_value = _make_conv(RuntimeError("LLM broke"))

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", _make_event(), send)
    assert captured[0].text == FALLBACK_TEXT


@pytest.mark.asyncio
async def test_bind_persistent_exception_falls_back(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    """``SessionBridge.bind_persistent`` 抛错也走 fallback。"""
    session_bridge.bind_persistent.side_effect = RuntimeError("session corrupt")

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", _make_event(), send)
    assert captured[0].text == FALLBACK_TEXT


@pytest.mark.asyncio
async def test_empty_output_falls_back(router: IMRouter, session_bridge: MagicMock) -> None:
    """LLM 跑通但一字未输出 → 回 ``FALLBACK_TEXT`` 而非空字符串。"""
    session_bridge.bind_persistent.return_value = _make_conv([TurnDone()])

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", _make_event(), send)
    assert captured[0].text == FALLBACK_TEXT


# ---------- ToolCall* 不污染 outbound ----------


@pytest.mark.asyncio
async def test_tool_call_events_not_in_outbound(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    """``ToolCallRequest`` / ``ToolCallResult`` 不应进入聚合 buffer。"""
    session_bridge.bind_persistent.return_value = _make_conv(
        [
            TextDelta(text="thinking..."),
            ToolCallRequest(tool_call_id="call-1", tool_name="web_search", args={"q": "天气"}),
            ToolCallResult(tool_call_id="call-1", tool_name="web_search", text="晴天 28°C"),
            TextDelta(text="今天是晴天 28°C"),
            TurnDone(),
        ]
    )

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", _make_event(), send)
    # 只 TextDelta 进 buffer,tool 文本(``晴天 28°C`` 在 ToolCallResult.text)不进
    assert captured[0].text == "thinking...今天是晴天 28°C"


# ---------- outbound 字段从 inbound 映射 ----------


@pytest.mark.asyncio
async def test_outbound_chat_fields_match_inbound(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    """``OutboundContent`` 的 ``chat_id`` / ``chat_scope`` /
    ``reply_to_message_id`` 跟 inbound event 一致。"""
    session_bridge.bind_persistent.return_value = _make_conv([TextDelta(text="ok"), TurnDone()])
    ev = _make_event(
        chat_id="OPENID-DEST",
        message_id="MSG-INBOUND-123",
        chat_scope="c2c",
    )

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", ev, send)
    assert captured[0].chat_id == "OPENID-DEST"
    assert captured[0].chat_scope == "c2c"
    assert captured[0].reply_to_message_id == "MSG-INBOUND-123"


# ---------- send 抛错不被吞 ----------


@pytest.mark.asyncio
async def test_send_callback_exception_propagates(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    """``send_fn`` 抛错应该向上抛(adapter 层处理),不应该被 Router 吞掉。

    理由:``send_fn`` 失败语义 = "回写 QQ 平台失败",这是 adapter 的责任域,
    Router 吞了会让 adapter 不知道 send 失败,无法记 status。
    """
    session_bridge.bind_persistent.return_value = _make_conv([TextDelta(text="hi"), TurnDone()])

    async def send_fail(_c: OutboundContent) -> None:
        raise ConnectionError("network down")

    with pytest.raises(ConnectionError, match="network down"):
        await router.handle_inbound("qq", _make_event(), send_fail)
