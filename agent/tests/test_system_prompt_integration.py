"""004 集成测：用 fake LLMClient 验证 system_prompt 三段语义。

覆盖 docs/requirements/004-engine-system-prompt-composer/design.md §7.2。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast

from agent.system_prompt import DESKTOP_CHAT_SURFACE, IM_QQ_SURFACE

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
    StaticSection,
    SystemPromptComposer,
    ToolRegistry,
    ToolResult,
)
from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
)


@dataclass
class _FakeLLMClient:
    """记录每次 ``complete`` / ``stream`` 收到的 messages，便于断言。

    005 起 ``stream`` 改 yield :class:`LLMStreamEvent` 序列；这里 yield 一段
    :class:`LLMTextDelta` 加一条 ``end_turn`` 的 :class:`LLMTurnDone` 模拟
    一次完整的"无工具调用"LLM turn。
    """

    reply: str = "ok"
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000  # 009：Conversation 经此推导预算阈值

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        self.received.append(messages)
        return self.reply

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(messages)
        yield LLMTextDelta(text=self.reply)
        yield LLMTurnDone(stop_reason="end_turn")


def _seed_user_persona(catalog: PersonaCatalog, name: str, body: str) -> str:
    """造一个 user persona 并返回其 id。"""
    info = catalog.create(name, body, description=f"{name} for test")
    return info.id


def _make_conversation(
    tmp_path: Path,
    *,
    persona_id: str,
    catalog: PersonaCatalog,
    fake_llm: _FakeLLMClient,
) -> Conversation:
    """组装一个完整 Conversation（注入 fake LLM、真 catalog、内存内 session store）。"""
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=persona_id,
    )
    store.create(session)
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    def builder_factory(pid: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, fake_llm),
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
    )


# ===== AC-1：默认装配下 system_prompt 含三段 =====


def test_default_system_prompt_contains_three_segments(tmp_path: Path) -> None:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    fake = _FakeLLMClient(reply="hi")
    conv = _make_conversation(
        tmp_path, persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog, fake_llm=fake
    )

    conv.send("hello")

    assert len(fake.received) == 1
    system_msg = fake.received[0][0]
    assert system_msg["role"] == "system"
    content = system_msg["content"]
    assert "项目元规则" in content
    assert "Echo" in content
    assert "关于过往对话的语言风格" in content


# ===== AC-4：切 persona 后下一轮 system_prompt 仍含切换策略段 =====


def test_strategy_segment_persists_after_persona_switch(tmp_path: Path) -> None:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    other_id = _seed_user_persona(
        catalog,
        name="cute_friend",
        body="你是一个非常可爱的朋友，说话总是带 ~ 和颜文字 (>_<)",
    )

    fake = _FakeLLMClient(reply="ok")
    conv = _make_conversation(
        tmp_path, persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog, fake_llm=fake
    )
    conv.send("first turn")

    conv.switch_persona(other_id)
    conv.send("second turn")

    assert len(fake.received) == 2
    second_system = fake.received[1][0]
    assert second_system["role"] == "system"
    content = second_system["content"]
    assert "关于过往对话的语言风格" in content
    assert "可爱的朋友" in content
    assert "Echo" not in content


# ===== AC-2 集成：注入自定义 strategy 后 system_prompt 反映自定义内容 =====


def test_custom_strategy_section_takes_effect(tmp_path: Path) -> None:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID

    composer = SystemPromptComposer.default(persona_id, catalog=catalog).with_section(
        StaticSection(
            key="persona_switch_strategy",
            text="测试用替换策略：忘记之前所有对话",
        )
    )
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog, composer=composer)

    output = builder.build()
    assert "测试用替换策略：忘记之前所有对话" in output
    assert "关于过往对话的语言风格" not in output


# ===== AC-3 集成：关闭 strategy 槽位后 system_prompt 不含该段 =====


def test_disabled_strategy_section_is_absent(tmp_path: Path) -> None:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID

    composer = SystemPromptComposer.default(persona_id, catalog=catalog).without(
        "persona_switch_strategy"
    )
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog, composer=composer)

    output = builder.build()
    assert "项目元规则" in output
    assert "Echo" in output
    assert "关于过往对话的语言风格" not in output


def test_persona_is_frozen_for_tool_loop_and_refreshes_next_turn(tmp_path: Path) -> None:
    class MutableCatalog:
        content = "persona A"

        def read_content(self, persona_id: str) -> str:
            return self.content

    class ChangingLLM(_FakeLLMClient):
        turn = 0

        def stream(
            self,
            messages: list[dict[str, Any]],
            *,
            tools: list[dict[str, Any]] | None = None,
            **overrides: Any,
        ) -> Iterator[LLMStreamEvent]:
            self.received.append(messages)
            self.turn += 1
            if self.turn == 1:
                catalog.content = "persona B"
                yield LLMToolCallDelta(
                    index=0,
                    tool_call_id="call-1",
                    tool_name="echo",
                    args_json_delta='{"message":"hi"}',
                )
                yield LLMTurnDone(stop_reason="tool_use")
                return
            yield LLMTextDelta(text="ok")
            yield LLMTurnDone(stop_reason="end_turn")

    class EchoTool:
        name: ClassVar[str] = "echo"
        description: ClassVar[str] = "echo"
        parameters_schema: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {},
        }

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult(text="ok")

    catalog = MutableCatalog()
    fake = ChangingLLM()
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    store.create(session)
    registry = ToolRegistry([EchoTool()])
    builder = MarkdownPromptBuilder(
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        catalog=cast(PersonaCatalog, catalog),
    )
    conv = Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, fake),
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        tool_registry=registry,
    )

    list(conv.stream("first", surface=DESKTOP_CHAT_SURFACE))
    assert "persona A" in fake.received[0][0]["content"]
    assert "persona A" in fake.received[1][0]["content"]
    assert '<surface type="desktop_chat" />' in "\n".join(
        message["content"] for message in fake.received[0]
    )
    assert '<surface type="desktop_chat" />' in "\n".join(
        message["content"] for message in fake.received[1]
    )
    assert fake.received[1][: len(fake.received[0])] == fake.received[0]

    conv.send("second")
    assert "persona B" in fake.received[2][0]["content"]


def test_surface_is_current_per_turn_and_user_text_cannot_override(tmp_path: Path) -> None:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    fake = _FakeLLMClient(reply="ok")
    conv = _make_conversation(
        tmp_path,
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        catalog=catalog,
        fake_llm=fake,
    )

    list(conv.stream("我在 QQ 吗？", surface=DESKTOP_CHAT_SURFACE))
    list(conv.stream("现在在哪？", surface=IM_QQ_SURFACE))
    list(conv.stream("旧入口"))

    contexts = [
        [
            message["content"]
            for message in request
            if message.get("content", "").startswith("<interaction_context>")
        ]
        for request in fake.received
    ]
    assert fake.received[0][-2]["role"] == "user"
    assert fake.received[0][-1]["role"] == "system"
    assert '<surface type="desktop_chat" />' in fake.received[0][-1]["content"]
    assert "否则不要在回复中提及交互端" in fake.received[0][-1]["content"]
    assert "不要告诉用户你收到了这段上下文" in fake.received[0][-1]["content"]
    assert fake.received[1][-2]["role"] == "user"
    assert fake.received[1][-1]["role"] == "system"
    assert '<surface type="im" provider="qq" />' in fake.received[1][-1]["content"]
    assert "否则不要在回复中提及交互端" in fake.received[1][-1]["content"]
    assert "不要告诉用户你收到了这段上下文" in fake.received[1][-1]["content"]
    assert [len(items) for items in contexts] == [1, 2, 3]
    assert '<surface type="desktop_chat" />' in contexts[0][-1]
    assert '<surface type="im" provider="qq" />' in contexts[1][-1]
    assert '<surface available="false" />' in contexts[2][-1]
    assert 'provider="qq"' not in contexts[2][-1]
    assert "取代更早的 interaction_context" in contexts[2][-1]

    # Model-only snapshots survive reload for prompt history, but never become chat messages.
    assert all("<interaction_context>" not in message.content for message in conv.session.messages)
    assert (
        sum("<interaction_context>" in message.content for message in conv.session.llm_messages)
        == 3
    )
    reloaded = JsonlSessionStore(base_dir=tmp_path / "sessions").load(conv.session.session_id)
    assert all("<interaction_context>" not in message.content for message in reloaded.messages)
    assert sum("<interaction_context>" in message.content for message in reloaded.llm_messages) == 3
