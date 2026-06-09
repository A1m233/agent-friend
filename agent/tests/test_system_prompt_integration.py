"""004 集成测：用 fake LLMClient 验证 system_prompt 三段语义。

覆盖 docs/requirements/004-engine-system-prompt-composer/design.md §7.2。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

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
)
from llm_providers import LLMClient, LLMStreamEvent, LLMTextDelta, LLMTurnDone


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
