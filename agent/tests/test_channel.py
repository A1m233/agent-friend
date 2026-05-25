"""007 起 channel 扩展的单元测试。

覆盖：

- :meth:`Session.new` 的 ``channel`` 参数行为（含 text 通道字节兼容）
- :attr:`Session.current_channel` 派生
- ``channel_change`` 事件支持
- :meth:`Conversation.switch_channel` 行为（落事件 / 幂等 / 校验）
- :class:`ChannelSection` 渲染（voice 输出 / text 渲染 None）
- :meth:`SystemPromptComposer.default` 在 ``session=None`` / 传入时的差异
- 老 session（无 ``initial_channel`` 字段）默认 fallback 到 ``"text"``
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    ChannelSection,
    Event,
    NaiveContextManager,
    NullSessionStore,
    PersonaCatalog,
    Session,
    SessionManager,
    SystemPromptComposer,
)

if TYPE_CHECKING:
    from agent import Conversation

# ===== Session 元字段 + 派生 =====


class TestSessionNewWithChannel:
    def test_default_channel_text_no_initial_channel_field(self) -> None:
        """默认 channel=text 时 session_meta.payload 不写 initial_channel——与 002~006 字节兼容。"""
        s = Session.new(title="t", persona="p", model="m")
        meta_payload = s.events[0].payload
        assert "initial_channel" not in meta_payload
        assert s.current_channel == "text"

    def test_voice_channel_writes_initial_channel(self) -> None:
        s = Session.new(title="t", persona="p", model="m", channel="voice")
        meta_payload = s.events[0].payload
        assert meta_payload["initial_channel"] == "voice"
        assert s.current_channel == "voice"


class TestCurrentChannelDerivation:
    def test_voice_initial(self) -> None:
        s = Session.new(title="t", persona="p", model="m", channel="voice")
        assert s.current_channel == "voice"

    def test_channel_change_event_takes_effect(self) -> None:
        s = Session.new(title="t", persona="p", model="m")
        s.append(
            Event(
                type="channel_change",
                uuid="evt-1",
                ts=datetime.now(UTC),
                payload={"from": "text", "to": "voice"},
            )
        )
        assert s.current_channel == "voice"

    def test_latest_change_wins(self) -> None:
        s = Session.new(title="t", persona="p", model="m", channel="voice")
        s.append(
            Event(
                type="channel_change",
                uuid="evt-1",
                ts=datetime.now(UTC),
                payload={"from": "voice", "to": "text"},
            )
        )
        s.append(
            Event(
                type="channel_change",
                uuid="evt-2",
                ts=datetime.now(UTC),
                payload={"from": "text", "to": "voice"},
            )
        )
        assert s.current_channel == "voice"

    def test_old_session_fallback_to_text(self) -> None:
        """模拟 002~006 时期的老 session：session_meta.payload 没有 initial_channel。"""
        s = Session.new(title="t", persona="p", model="m")
        # 默认就是没有 initial_channel
        meta_payload = s.events[0].payload
        assert "initial_channel" not in meta_payload
        assert s.current_channel == "text"

    def test_invalid_channel_value_in_event_falls_back(self) -> None:
        """事件 payload.to 是非法值时，跳到上一个有效事件 / fallback 到 initial。"""
        s = Session.new(title="t", persona="p", model="m", channel="voice")
        s.append(
            Event(
                type="channel_change",
                uuid="evt-1",
                ts=datetime.now(UTC),
                payload={"from": "voice", "to": "garbage"},
            )
        )
        # 当前实现：跳过非法 to 继续向前找；找不到时回 initial_channel。
        # 更稳健的兜底写法是直接 fallback；本测试断言至少不抛错且返回合法值之一。
        assert s.current_channel in ("voice", "text")


# ===== SessionManager.create + channel =====


class TestSessionManagerCreateChannel:
    def test_create_with_voice_channel(self, tmp_path: Path) -> None:
        store = NullSessionStore()
        mgr = SessionManager(store=store)
        s = mgr.create(persona="default", model="m", channel="voice")
        assert s.current_channel == "voice"

    def test_create_default_channel_text(self) -> None:
        store = NullSessionStore()
        mgr = SessionManager(store=store)
        s = mgr.create(persona="default", model="m")
        assert s.current_channel == "text"

    def test_invalid_channel_raises(self) -> None:
        store = NullSessionStore()
        mgr = SessionManager(store=store)
        with pytest.raises(ValueError, match="channel"):
            mgr.create(persona="default", model="m", channel="garbage")


# ===== ChannelSection 渲染 =====


class TestChannelSectionRender:
    def test_voice_channel_renders_template(self) -> None:
        s = Session.new(title="t", persona="p", model="m", channel="voice")
        section = ChannelSection(
            key="channel",
            session=s,
            voice_template="VOICE_INSTRUCTION_TEXT",
        )
        assert section.render() == "VOICE_INSTRUCTION_TEXT"

    def test_text_channel_returns_none(self) -> None:
        s = Session.new(title="t", persona="p", model="m")  # text
        section = ChannelSection(
            key="channel",
            session=s,
            voice_template="VOICE_INSTRUCTION_TEXT",
        )
        assert section.render() is None

    def test_channel_change_after_init_takes_effect(self) -> None:
        """ChannelSection 持有 session 引用，channel_change 事件落到 session 后下次 render 应反映新值。"""
        s = Session.new(title="t", persona="p", model="m")
        section = ChannelSection(
            key="channel",
            session=s,
            voice_template="VOICE_INSTRUCTION_TEXT",
        )
        assert section.render() is None  # text initial

        s.append(
            Event(
                type="channel_change",
                uuid="evt-1",
                ts=datetime.now(UTC),
                payload={"from": "text", "to": "voice"},
            )
        )
        assert section.render() == "VOICE_INSTRUCTION_TEXT"

    def test_voice_template_strip(self) -> None:
        s = Session.new(title="t", persona="p", model="m", channel="voice")
        section = ChannelSection(
            key="channel",
            session=s,
            voice_template="   \n\nABC\n\n   ",
        )
        assert section.render() == "ABC"

    def test_empty_voice_template_returns_none(self) -> None:
        s = Session.new(title="t", persona="p", model="m", channel="voice")
        section = ChannelSection(key="channel", session=s, voice_template="")
        assert section.render() is None


# ===== SystemPromptComposer.default 与 session 注入 =====


class TestComposerDefaultWithSession:
    def test_text_session_channel_section_yields_no_output(self, tmp_path: Path) -> None:
        """text session 装配 ChannelSection 后，compose 输出应不含通道指令文本。"""
        catalog = PersonaCatalog(external_dir=tmp_path)
        s = Session.new(title="t", persona="default", model="m")
        composer = SystemPromptComposer.default(
            BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog, session=s
        )
        output = composer.compose()
        assert "正在通过电话和用户对话" not in output

    def test_voice_session_channel_section_yields_voice_text(self, tmp_path: Path) -> None:
        """voice session 装配后，compose 输出应包含通道指令文本。"""
        catalog = PersonaCatalog(external_dir=tmp_path)
        s = Session.new(title="t", persona="default", model="m", channel="voice")
        composer = SystemPromptComposer.default(
            BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog, session=s
        )
        output = composer.compose()
        assert "正在通过电话和用户对话" in output


# ===== Conversation.switch_channel =====


class TestConversationSwitchChannel:
    """``switch_channel`` 的事件落盘与幂等性。

    用 NullSessionStore 即可覆盖语义；不需要真实 LLM。
    """

    def _make_conversation(self) -> tuple[Conversation, Session]:
        from agent import Conversation, MarkdownPromptBuilder
        from llm_providers import LLMClient, ProviderSpec

        catalog = PersonaCatalog()
        store = NullSessionStore()
        session = Session.new(
            title="t", persona="default", model="deepseek/deepseek-chat", channel="text"
        )
        # 这些不会被实际调用（不发消息），仅装配
        spec = ProviderSpec(model="deepseek/deepseek-chat", api_key="sk-test")
        llm = LLMClient(spec)
        prompt_builder = MarkdownPromptBuilder(
            persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog, session=session
        )
        conv = Conversation(
            session=session,
            store=store,
            llm_client=llm,
            context_manager=NaiveContextManager(),
            prompt_builder=prompt_builder,
            catalog=catalog,
        )
        return conv, session

    def test_switch_to_voice_appends_event(self) -> None:
        conv, session = self._make_conversation()
        before = len(session.events)
        conv.switch_channel("voice")
        assert len(session.events) == before + 1
        last = session.events[-1]
        assert last.type == "channel_change"
        assert last.payload == {"from": "text", "to": "voice"}
        assert session.current_channel == "voice"

    def test_idempotent_same_channel(self) -> None:
        conv, session = self._make_conversation()
        before = len(session.events)
        conv.switch_channel("text")  # 当前已经是 text
        assert len(session.events) == before
        assert session.current_channel == "text"

    def test_invalid_channel_raises(self) -> None:
        conv, _ = self._make_conversation()
        with pytest.raises(ValueError, match="channel"):
            conv.switch_channel("garbage")

    def test_switch_back_appends_another_event(self) -> None:
        conv, session = self._make_conversation()
        conv.switch_channel("voice")
        conv.switch_channel("text")
        events = [e for e in session.events if e.type == "channel_change"]
        assert len(events) == 2
        assert events[0].payload["to"] == "voice"
        assert events[1].payload["to"] == "text"
        assert session.current_channel == "text"


# ===== JSONL 序列化往返：channel_change 事件 =====


class TestChannelChangeEventSerialization:
    def test_jsonl_roundtrip(self) -> None:
        ev = Event(
            type="channel_change",
            uuid="evt-1",
            ts=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
            payload={"from": "text", "to": "voice"},
            meta={},
        )
        line = ev.to_jsonl()
        restored = Event.from_jsonl(line)
        assert restored == ev
