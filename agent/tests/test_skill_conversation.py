from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from agent.skills import SkillCatalogService, SkillSelection

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
)
from llm_providers import LLMClient, LLMStreamEvent, LLMTextDelta, LLMTurnDone


@dataclass
class _RecordingLLM:
    context_window: int = 128_000
    received: list[list[dict[str, Any]]] = field(default_factory=list)

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(messages)
        yield LLMTextDelta("ok")
        yield LLMTurnDone(stop_reason="end_turn")


def _write_skill(root: Path, name: str, body: str) -> Path:
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    entry = package / "SKILL.md"
    entry.write_text(
        f"---\nname: {name}\ndescription: Test workflow\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return entry


def _conversation(
    tmp_path: Path,
) -> tuple[Conversation, _RecordingLLM, SkillCatalogService, SkillSelection]:
    personal = tmp_path / "skills"
    _write_skill(personal, "test-skill", "FIRST VERSION")
    skill_service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = skill_service.initialize()
    skill = next(item for item in snapshot.entries if item.name == "test-skill")

    persona_catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    session = Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    store = JsonlSessionStore(tmp_path / "sessions")
    store.create(session)
    llm = _RecordingLLM()

    def builder_factory(persona_id: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, catalog=persona_catalog)

    conversation = Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, llm),
        context_manager=NaiveContextManager(),
        prompt_builder=builder_factory(BUILTIN_DEFAULT_PERSONA_ID),
        prompt_builder_factory=builder_factory,
        catalog=persona_catalog,
        skill_service=skill_service,
    )
    return conversation, llm, skill_service, SkillSelection(skill.id, skill.version)


def _reopen_conversation(
    tmp_path: Path,
    *,
    session: Session,
    store: JsonlSessionStore,
    skill_service: SkillCatalogService,
) -> tuple[Conversation, _RecordingLLM]:
    persona_catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    llm = _RecordingLLM()

    def builder_factory(persona_id: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, catalog=persona_catalog)

    conversation = Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, llm),
        context_manager=NaiveContextManager(),
        prompt_builder=builder_factory(BUILTIN_DEFAULT_PERSONA_ID),
        prompt_builder_factory=builder_factory,
        catalog=persona_catalog,
        skill_service=skill_service,
    )
    return conversation, llm


def test_skill_context_order_persistence_and_general_projection(tmp_path: Path) -> None:
    conversation, llm, _, selection = _conversation(tmp_path)

    list(
        conversation.stream(
            "hello",
            user_event_id="user-1",
            skill_selections=(selection,),
            turn_context="EXISTING TURN CONTEXT",
        )
    )

    messages = llm.received[0]
    catalog_index = next(
        index for index, item in enumerate(messages) if "<skills_catalog>" in item["content"]
    )
    user_index = next(index for index, item in enumerate(messages) if item["content"] == "hello")
    skill_index = next(
        index for index, item in enumerate(messages) if "FIRST VERSION" in item["content"]
    )
    existing_context_index = next(
        index for index, item in enumerate(messages) if item["content"] == "EXISTING TURN CONTEXT"
    )
    assert catalog_index < user_index < skill_index
    assert skill_index < existing_context_index
    assert [event.type for event in conversation.session.events[1:5]] == [
        "skill_catalog_state",
        "user_message",
        "skill_context",
        "assistant_message",
    ]
    assert [message.content for message in conversation.session.messages] == ["hello", "ok"]
    assert any(
        message.meta.get("synthetic") == "skill_instructions"
        for message in conversation.session.llm_messages
    )


def test_plain_query_does_not_activate_and_unchanged_catalog_is_not_appended(
    tmp_path: Path,
) -> None:
    conversation, llm, _, selection = _conversation(tmp_path)
    list(conversation.stream("first", skill_selections=(selection,)))
    list(conversation.stream("please use $test-skill"))

    assert sum(event.type == "skill_catalog_state" for event in conversation.session.events) == 1
    assert sum(event.type == "skill_context" for event in conversation.session.events) == 1
    current_index = next(
        index
        for index, item in enumerate(llm.received[-1])
        if item["content"] == "please use $test-skill"
    )
    assert all("<skill>" not in item["content"] for item in llm.received[-1][current_index + 1 :])


def test_reactivation_reads_current_version_and_idempotency_checks_selection(
    tmp_path: Path,
) -> None:
    conversation, llm, service, selection = _conversation(tmp_path)
    list(conversation.stream("first", user_event_id="stable-id", skill_selections=(selection,)))
    first_call_count = len(llm.received)

    replay = list(
        conversation.stream("first", user_event_id="stable-id", skill_selections=(selection,))
    )
    assert len(llm.received) == first_call_count
    assert replay

    with pytest.raises(ValueError, match="不同 Skill"):
        list(conversation.stream("first", user_event_id="stable-id", skill_selections=()))

    record = next(item for item in service.snapshot.entries if item.id == selection.id)
    record.entry_path.write_text(
        "---\nname: test-skill\ndescription: Test workflow\n---\n\nSECOND VERSION\n",
        encoding="utf-8",
    )
    list(conversation.stream("again", skill_selections=(SkillSelection(selection.id),)))
    assert any("SECOND VERSION" in item["content"] for item in llm.received[-1])


def test_unknown_structured_selection_becomes_model_visible_load_error(tmp_path: Path) -> None:
    conversation, llm, _, _ = _conversation(tmp_path)

    list(conversation.stream("continue", skill_selections=(SkillSelection("missing"),)))

    assert any("<skill_load_error>" in item["content"] for item in llm.received[0])
    skill_event = next(
        event for event in conversation.session.events if event.type == "skill_context"
    )
    assert skill_event.payload["error_code"] == "skill_not_found"


def test_restart_replays_frozen_skill_context_without_reactivation(tmp_path: Path) -> None:
    conversation, _, service, selection = _conversation(tmp_path)
    list(conversation.stream("first", skill_selections=(selection,)))

    record = next(item for item in service.snapshot.entries if item.id == selection.id)
    record.entry_path.write_text(
        "---\nname: test-skill\ndescription: Test workflow\n---\n\nSECOND VERSION\n",
        encoding="utf-8",
    )
    service.refresh()

    store = JsonlSessionStore(tmp_path / "sessions")
    loaded = store.load(conversation.session.session_id)
    reopened, llm = _reopen_conversation(
        tmp_path,
        session=loaded,
        store=store,
        skill_service=service,
    )
    skill_event_count = sum(event.type == "skill_context" for event in loaded.events)

    list(reopened.stream("after restart"))

    messages = llm.received[0]
    current_user_index = next(
        index for index, item in enumerate(messages) if item["content"] == "after restart"
    )
    assert any("FIRST VERSION" in item["content"] for item in messages[:current_user_index])
    assert all("SECOND VERSION" not in item["content"] for item in messages)
    assert all("<skill>" not in item["content"] for item in messages[current_user_index + 1 :])
    assert sum(event.type == "skill_context" for event in loaded.events) == skill_event_count


def test_edit_resend_deactivates_old_skill_without_implicit_reselection(tmp_path: Path) -> None:
    conversation, llm, _, selection = _conversation(tmp_path)
    list(conversation.stream("old question", skill_selections=(selection,)))

    list(
        conversation.edit_resend_latest(
            "new question",
            expected_user_content="old question",
        )
    )

    assert all("FIRST VERSION" not in item["content"] for item in llm.received[-1])
    assert sum(event.type == "skill_context" for event in conversation.session.events) == 1
    assert all(event.type != "skill_context" for event in conversation.session.active_events)
