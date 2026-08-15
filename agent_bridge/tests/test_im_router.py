"""IMRouter 单测(M22.3)。

覆盖 design.md §3.2 + progress.md §M22.3 单测点:

- ``session_id_for`` 稳定输出 + 同 chat_id 同结果
- ``handle_inbound`` 调用 ``session_bridge.bind_persistent`` 时 ``thread_id`` ==
  ``session_id_for(...)``
- mock ``Conversation.stream`` 返回 ``[TextDelta("hello"), TextDelta(" world"),
  TurnDone()]`` → ``send_fn`` 收到 ``OutboundContent.text == "hello world"``
- agent 主链路正常输出声明为 Markdown,channel fallback 仍声明为纯文本
- mock ``Conversation.stream`` 抛错 → ``send_fn`` 收到
  ``OutboundContent.text == FALLBACK_TEXT``
- ``ToolCallRequest`` / ``ToolCallResult`` 不污染 outbound(只 TextDelta 进 buffer)
- ``send_fn`` 收到的 ``chat_id`` / ``chat_scope`` / ``reply_to_message_id`` 跟
  inbound event 一致
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from agent.runtime import AgentRuntime, RunOutcome
from agent.system_prompt import IM_QQ_SURFACE
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.event import InboundEvent
from agent_bridge.protocols.im.media import InboundMedia
from agent_bridge.protocols.im.router import FALLBACK_TEXT, IMRouter
from agent_bridge.session_bridge import PersistentBootstrap

from agent import TextDelta, ToolCallRequest, ToolCallResult, TurnDone
from llm_providers import LLMTransientProviderError


class StubStreamSession:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.updates: list[str] = []
        self.finishes: list[str] = []
        self.aborts: list[tuple[str, str]] = []
        self._fail_first = fail_first
        self._failed = False

    @property
    def has_started(self) -> bool:
        return bool(self.updates)

    @property
    def is_completed(self) -> bool:
        return bool(self.finishes)

    @property
    def is_failed(self) -> bool:
        return self._failed

    async def update(self, full_text: str) -> None:
        if self._fail_first:
            self._failed = True
            return
        self.updates.append(full_text)

    async def finish(self, full_text: str) -> None:
        self.finishes.append(full_text)

    async def abort(self, full_text: str, reason: str) -> None:
        self.aborts.append((full_text, reason))


class StubStreamingProvider:
    def __init__(self, stream: StubStreamSession | None) -> None:
        self.stream = stream
        self.sent: list[OutboundContent] = []
        self.contexts: list[Any] = []

    async def send(self, content: OutboundContent) -> None:
        self.sent.append(content)

    def open_text_stream(self, context: Any) -> StubStreamSession | None:
        self.contexts.append(context)
        return self.stream


class StubBoundProvider:
    type = "qq"
    bind_id = "BOUND-ACCOUNT"

    def __init__(self) -> None:
        self.sent: list[OutboundContent] = []

    async def send(self, content: OutboundContent) -> None:
        self.sent.append(content)


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

        def _raise(_user_input: str, **_kwargs: Any) -> Any:
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


def test_runtime_conversation_factory_delegates_post_turn_observe(
    session_bridge: MagicMock,
) -> None:
    """AgentRuntime 调度的 IM Conversation 必须关闭内部 observe，避免同轮双写。"""
    conv = _make_conv([TurnDone()])
    session_bridge.bind_persistent.return_value = conv
    runtime = MagicMock()
    handle = MagicMock()
    handle.events.return_value = iter([TurnDone()])
    handle.terminal = SimpleNamespace(
        outcome=RunOutcome.COMPLETED,
        error=None,
        reason_code="",
    )

    def submit(_event: object, **kwargs: Any) -> MagicMock:
        kwargs["conversation_factory"]()
        return handle

    runtime.submit.side_effect = submit
    runtime_router = IMRouter(
        session_bridge=session_bridge,
        default_persona="default-persona",
        default_model="default-model",
        agent_runtime=cast(AgentRuntime, runtime),
    )
    boot = PersistentBootstrap(
        thread_id="im:qq:OPENID-A",
        new_user_input="hello",
        default_persona="default-persona",
        default_model="default-model",
    )

    runtime_router._run_turn_sync(boot)

    session_bridge.bind_persistent.assert_called_once_with(
        boot,
        post_turn_external=True,
    )


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
    assert captured[0].text_format == "markdown"
    session_bridge.bind_persistent.return_value.stream.assert_called_once_with(
        "你好",
        attachments=[],
        channel="text",
        turn_context=None,
        surface=IM_QQ_SURFACE,
        interaction_destination_id=None,
    )


@pytest.mark.asyncio
async def test_bound_provider_freezes_exact_im_destination_for_agent_tools(
    router: IMRouter,
    session_bridge: MagicMock,
) -> None:
    conv = _make_conv([TextDelta(text="请回电脑端授权。"), TurnDone()])
    session_bridge.bind_persistent.return_value = conv
    provider = StubBoundProvider()
    event = _make_event(chat_id="OPENID-USER-A", chat_scope="c2c")

    await router.handle_inbound("qq", event, provider)

    conv.stream.assert_called_once_with(
        "你好",
        attachments=[],
        channel="text",
        turn_context=None,
        surface=IM_QQ_SURFACE,
        interaction_destination_id=("qq|BOUND-ACCOUNT|c2c|OPENID-USER-A|im:qq:OPENID-USER-A"),
    )
    assert provider.sent[0].text == "请回电脑端授权。"


@pytest.mark.asyncio
async def test_media_boundary_is_given_to_model_and_prefixed_for_user(
    router: IMRouter,
    session_bridge: MagicMock,
) -> None:
    conv = _make_conv([TextDelta(text="先回答你的文字。"), TurnDone()])
    session_bridge.bind_persistent.return_value = conv
    media = (
        InboundMedia(
            source="im_attachment",
            kind="animated_image",
            fidelity="unsupported",
            reason="media_gif_unsupported",
            display_name="sticker.gif",
        ),
    )
    captured: list[OutboundContent] = []

    async def send(content: OutboundContent) -> None:
        captured.append(content)

    await router.handle_inbound(
        "qq",
        _make_event(content="先看文字"),
        send,
        media=media,
    )

    assert captured[0].text.startswith("（你发的 GIF 我暂时看不了")
    assert captured[0].text.endswith("先回答你的文字。")
    turn_context = conv.stream.call_args.kwargs["turn_context"]
    assert '"fidelity": "unsupported"' in turn_context
    assert "不得声称看到了未提供的图像" in turn_context


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

    def stream(_user_input: str, **kwargs: Any) -> Any:
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


def test_fallback_text_is_plain_without_robot_emoji() -> None:
    assert FALLBACK_TEXT == "这轮回答中断了，没有生成完整回复。请稍后重试。"


@pytest.mark.asyncio
async def test_stream_exception_falls_back(router: IMRouter, session_bridge: MagicMock) -> None:
    """``Conversation.stream`` 抛错 → 持久化失败状态并回 ``FALLBACK_TEXT``。"""
    conv = _make_conv(RuntimeError("LLM broke"))
    session_bridge.bind_persistent.return_value = conv

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", _make_event(), send)
    assert captured[0].text == FALLBACK_TEXT
    assert captured[0].text_format == "plain_text"
    conv.append_run_error_event.assert_called_once_with(
        code="internal_error",
        message="这轮回答中断了，没有生成完整回复。请稍后重试。",
        recoverable=False,
        source="im",
        run_id=None,
        sanitize_failed_model_call=False,
    )


@pytest.mark.asyncio
async def test_engine_overload_uses_specific_fallback(
    router: IMRouter,
    session_bridge: MagicMock,
) -> None:
    conv = _make_conv(
        LLMTransientProviderError(
            "provider busy",
            provider_error_type="engine_overloaded_error",
            status_code=429,
            provider_request_id="request-overload-1",
        )
    )
    session_bridge.bind_persistent.return_value = conv
    captured: list[OutboundContent] = []

    async def send(content: OutboundContent) -> None:
        captured.append(content)

    await router.handle_inbound("qq", _make_event(), send)

    expected = "模型服务暂时过载，这次没能生成回答。稍后再试一下。"
    assert captured[0].text == expected
    assert captured[0].text_format == "plain_text"
    conv.append_run_error_event.assert_called_once_with(
        code="upstream_transient",
        message=expected,
        recoverable=True,
        source="im",
        run_id=None,
        sanitize_failed_model_call=False,
    )


@pytest.mark.asyncio
async def test_unregistered_adapter_mapping_fails_before_llm(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    captured: list[OutboundContent] = []

    async def send(content: OutboundContent) -> None:
        captured.append(content)

    await router.handle_inbound("test-im", _make_event(), send)

    session_bridge.bind_persistent.assert_not_called()
    assert captured[0].text == FALLBACK_TEXT
    assert captured[0].text_format == "plain_text"


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
    assert captured[0].text_format == "plain_text"


@pytest.mark.asyncio
async def test_empty_output_falls_back(router: IMRouter, session_bridge: MagicMock) -> None:
    """LLM 跑通但一字未输出 → 回 ``FALLBACK_TEXT`` 而非空字符串。"""
    session_bridge.bind_persistent.return_value = _make_conv([TurnDone()])

    captured: list[OutboundContent] = []

    async def send(c: OutboundContent) -> None:
        captured.append(c)

    await router.handle_inbound("qq", _make_event(), send)
    assert captured[0].text == FALLBACK_TEXT
    assert captured[0].text_format == "plain_text"


# ---------- ToolCall* 不污染 outbound，但保留文本边界 ----------


@pytest.mark.asyncio
async def test_tool_call_events_not_in_outbound(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    """工具载荷不进入 buffer，工具前后的 assistant 文本保留段落边界。"""
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
    assert captured[0].text == "thinking...\n\n今天是晴天 28°C"


@pytest.mark.asyncio
async def test_tool_boundary_reuses_existing_newlines(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    session_bridge.bind_persistent.return_value = _make_conv(
        [
            TextDelta(text="正在处理\n"),
            ToolCallRequest(tool_call_id="call-1", tool_name="todo_manage", args={}),
            ToolCallResult(tool_call_id="call-1", tool_name="todo_manage", text="ok"),
            TextDelta(text="\n已完成"),
            TurnDone(),
        ]
    )

    captured: list[OutboundContent] = []

    async def send(content: OutboundContent) -> None:
        captured.append(content)

    await router.handle_inbound("qq", _make_event(), send)

    assert captured[0].text == "正在处理\n\n已完成"


@pytest.mark.asyncio
async def test_native_stream_includes_tool_boundary_in_updates_and_finish(
    router: IMRouter, session_bridge: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_bridge.bind_persistent.return_value = _make_conv(
        [
            TextDelta(text="好，添加一条 todo。"),
            ToolCallRequest(tool_call_id="call-1", tool_name="todo_manage", args={}),
            ToolCallResult(tool_call_id="call-1", tool_name="todo_manage", text="ok"),
            TextDelta(text="搞定了。"),
            TurnDone(),
        ]
    )
    stream = StubStreamSession()
    provider = StubStreamingProvider(stream)

    async def no_delay(_delay: float) -> None:
        return None

    monkeypatch.setattr("agent_bridge.protocols.im.router.asyncio.sleep", no_delay)

    await router.handle_inbound("qq", _make_event(), provider)

    expected = "好，添加一条 todo。\n\n搞定了。"
    assert stream.updates[-1] == expected
    assert stream.finishes == [expected]


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


# ---------- native stream routing ----------


@pytest.mark.asyncio
async def test_native_stream_sends_body_once_then_separate_menu(
    router: IMRouter, session_bridge: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_bridge.bind_persistent.return_value = _make_conv(
        [TextDelta(text="hello"), TextDelta(text=" world"), TurnDone()]
    )
    stream = StubStreamSession()
    provider = StubStreamingProvider(stream)
    sleep_delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr("agent_bridge.protocols.im.router.asyncio.sleep", fake_sleep)

    await router.handle_inbound("qq", _make_event(), provider)

    assert stream.updates
    assert stream.updates[-1] == "hello world"
    assert stream.finishes == ["hello world"]
    assert [item.text for item in provider.sent] == ["操作"]
    assert provider.sent[0].controls is not None
    assert all(item.text != "hello world" for item in provider.sent)
    assert sleep_delays[-1] == pytest.approx(0.8)
    assert provider.contexts[0].session_id == "im:qq:OPENID-USER-A"
    assert isinstance(provider.contexts[0].latency_started_ms, int)


@pytest.mark.asyncio
async def test_native_stream_first_failure_falls_back_to_static_body(
    router: IMRouter, session_bridge: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_bridge.bind_persistent.return_value = _make_conv(
        [TextDelta(text="complete answer"), TurnDone()]
    )
    stream = StubStreamSession(fail_first=True)
    provider = StubStreamingProvider(stream)
    sleep_delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr("agent_bridge.protocols.im.router.asyncio.sleep", fake_sleep)

    await router.handle_inbound("qq", _make_event(), provider)

    assert stream.has_started is False
    assert stream.finishes == []
    assert [item.text for item in provider.sent] == ["complete answer"]
    assert provider.sent[0].controls is not None
    assert 0.8 not in sleep_delays


@pytest.mark.asyncio
async def test_native_stream_agent_error_aborts_partial_without_static_duplicate(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    def stream(_user_input: str, **_kwargs: Any) -> Any:
        yield TextDelta(text="partial answer")
        raise RuntimeError("agent failed")

    conv = MagicMock()
    conv.stream.side_effect = stream
    session_bridge.bind_persistent.return_value = conv
    stream_session = StubStreamSession()
    provider = StubStreamingProvider(stream_session)

    await router.handle_inbound("qq", _make_event(), provider)

    assert stream_session.has_started is True
    assert len(stream_session.aborts) == 1
    assert stream_session.aborts[0][0].startswith("partial answer")
    assert "回复生成中断" in stream_session.aborts[0][0]
    assert provider.sent == []


@pytest.mark.asyncio
async def test_inbound_attachments_allow_text_output_streaming(
    router: IMRouter, session_bridge: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.attachments import AttachmentRef

    session_bridge.persistent_model.return_value = "kimi/kimi-k2.7-code"
    session_bridge.bind_persistent.return_value = _make_conv(
        [TextDelta(text="attachment answer"), TurnDone()]
    )
    stream = StubStreamSession()
    provider = StubStreamingProvider(stream)
    attachment = AttachmentRef(
        asset_id="asset-1",
        kind="image",
        media_type="image/png",
        display_name="a.png",
        size_bytes=10,
        sha256="0" * 64,
    )

    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("agent_bridge.protocols.im.router.asyncio.sleep", fake_sleep)

    await router.handle_inbound("qq", _make_event(), provider, attachments=(attachment,))

    assert len(provider.contexts) == 1
    assert stream.updates[-1] == "attachment answer"
    assert stream.finishes == ["attachment answer"]
    assert [item.text for item in provider.sent] == ["操作"]


@pytest.mark.asyncio
async def test_unsupported_inbound_attachment_does_not_open_stream(
    router: IMRouter, session_bridge: MagicMock
) -> None:
    from agent.attachments import AttachmentRef

    session_bridge.persistent_model.return_value = "deepseek/deepseek-v4-flash"
    provider = StubStreamingProvider(StubStreamSession())
    attachment = AttachmentRef(
        asset_id="asset-unsupported",
        kind="image",
        media_type="image/png",
        display_name="a.png",
        size_bytes=10,
        sha256="0" * 64,
    )

    await router.handle_inbound("qq", _make_event(), provider, attachments=(attachment,))

    assert provider.contexts == []
    assert [item.text for item in provider.sent] == [
        "当前会话使用的模型不支持这类附件，请先切换到支持图片或文档的模型。"
    ]
