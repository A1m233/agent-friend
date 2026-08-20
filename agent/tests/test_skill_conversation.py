from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from agent.command_runtime.tools import COMMAND_SKILL_SNAPSHOT_ARG
from agent.skills import SkillCatalogService, SkillSelection
from agent.tools import ToolRegistry
from agent.tools.builtin.skills import (
    SKILL_CATALOG_SNAPSHOT_ARG,
    SKILL_RUNTIME_CAPABILITIES_ARG,
    SkillReadTool,
)

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
    SkillActivationStatus,
    ToolId,
)
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
)


@dataclass
class _RecordingLLM:
    context_window: int = 128_000
    received: list[list[dict[str, Any]]] = field(default_factory=list)

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(request.messages)
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

    registry = ToolRegistry([SkillReadTool(skill_service)])
    assert registry.identity("skill_read") == ToolId(namespace="skills", name="skill_read")
    conversation = Conversation(
        session=session,
        store=store,
        llm_client=llm,
        context_manager=NaiveContextManager(),
        prompt_builder=builder_factory(BUILTIN_DEFAULT_PERSONA_ID),
        prompt_builder_factory=builder_factory,
        catalog=persona_catalog,
        skill_service=skill_service,
        tool_registry=registry,
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
        llm_client=llm,
        context_manager=NaiveContextManager(),
        prompt_builder=builder_factory(BUILTIN_DEFAULT_PERSONA_ID),
        prompt_builder_factory=builder_factory,
        catalog=persona_catalog,
        skill_service=skill_service,
        tool_registry=ToolRegistry([SkillReadTool(skill_service)]),
    )
    return conversation, llm


def test_skill_context_order_persistence_and_general_projection(tmp_path: Path) -> None:
    conversation, llm, _, selection = _conversation(tmp_path)

    events = list(
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
    assert "No governed filesystem search/read tools" in messages[skill_index]["content"]
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
    assert [event for event in events if isinstance(event, SkillActivationStatus)] == [
        SkillActivationStatus(
            status="activated",
            skill_id=selection.id,
            name="test-skill",
            source="personal",
            reason="由宿主从当前用户轮的显式选择中解析",
        )
    ]


def test_plain_query_does_not_activate_and_unchanged_catalog_is_not_appended(
    tmp_path: Path,
) -> None:
    conversation, llm, _, selection = _conversation(tmp_path)
    list(conversation.stream("first", skill_selections=(selection,)))
    list(conversation.stream("test-skill 是什么？"))

    assert sum(event.type == "skill_catalog_state" for event in conversation.session.events) == 1
    assert sum(event.type == "skill_context" for event in conversation.session.events) == 1
    current_index = next(
        index
        for index, item in enumerate(llm.received[-1])
        if item["content"] == "test-skill 是什么？"
    )
    assert all("<skill>" not in item["content"] for item in llm.received[-1][current_index + 1 :])


def test_changed_catalog_keeps_trusted_prior_state_in_uncompacted_context(
    tmp_path: Path,
) -> None:
    conversation, llm, service, _ = _conversation(tmp_path)
    list(conversation.stream("first catalog"))
    _write_skill(service.personal_root, "later-skill", "LATER VERSION")
    service.refresh()

    list(conversation.stream("compare catalogs"))

    catalogs = [
        item["content"] for item in llm.received[-1] if "<skills_catalog>" in item["content"]
    ]
    assert len(catalogs) == 2
    assert "later-skill" not in catalogs[0]
    assert "later-skill" in catalogs[1]


@dataclass
class _SkillCallingLLM:
    skill_id: str
    context_window: int = 128_000
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    received_tools: list[list[dict[str, Any]] | None] = field(default_factory=list)

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        messages = request.messages
        self.received.append(messages)
        self.received_tools.append(request.tools)
        if len(self.received) == 1:
            yield LLMTextDelta("我会使用 test-skill，因为你指的是列表中的第 2 个 Skill。")
            yield LLMToolCallDelta(
                index=0,
                tool_call_id="skill-call-1",
                tool_name="skill_read",
                args_json_delta=json.dumps(
                    {
                        "skill_id": self.skill_id,
                        "reason": "当前任务与 Test workflow 匹配",
                    },
                    ensure_ascii=False,
                ),
            )
            yield LLMTurnDone(stop_reason="tool_use")
            return
        assert any(
            item.get("role") == "tool" and "FIRST VERSION" in item.get("content", "")
            for item in messages
        )
        yield LLMTextDelta("test-skill 已成功加载，我会按它的说明继续。")
        yield LLMTurnDone(stop_reason="end_turn")


def test_model_can_implicitly_select_and_read_skill_in_tool_loop(tmp_path: Path) -> None:
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
    llm = _SkillCallingLLM(skill.id)

    def builder_factory(persona_id: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, catalog=persona_catalog)

    conversation = Conversation(
        session=session,
        store=store,
        llm_client=llm,
        context_manager=NaiveContextManager(),
        prompt_builder=builder_factory(BUILTIN_DEFAULT_PERSONA_ID),
        prompt_builder_factory=builder_factory,
        catalog=persona_catalog,
        skill_service=skill_service,
        tool_registry=ToolRegistry([SkillReadTool(skill_service)]),
    )

    events = list(conversation.stream("帮我完成这个 Test workflow"))

    assert len(llm.received) == 2
    assert llm.received_tools[0] is not None
    assert any(spec["function"]["name"] == "skill_read" for spec in llm.received_tools[0] or [])
    result_event = next(
        event for event in conversation.session.events if event.type == "tool_call_result"
    )
    assert result_event.payload["is_error"] is False
    assert "No governed filesystem search/read tools" in result_event.payload["content"]
    assert result_event.meta["extra"]["skill_activation"]["status"] == "activated"
    assert any(getattr(event, "type", "") == "tool_call_result" for event in events)


def test_semantic_skill_activation_is_available_to_later_command_binding(
    tmp_path: Path,
) -> None:
    conversation, _, service, selection = _conversation(tmp_path)
    snapshot = service.begin_turn()
    conversation._active_skill_turn_snapshot = snapshot
    result = SkillReadTool(service).invoke(
        {
            "skill_id": selection.id,
            "reason": "test",
            SKILL_CATALOG_SNAPSHOT_ARG: snapshot.catalog,
            SKILL_RUNTIME_CAPABILITIES_ARG: "test capabilities",
        }
    )

    conversation._adopt_runtime_skill_activation(result)
    invocation_args = conversation._tool_args_with_invocation_context(
        "exec_command",
        {},
        current_turn_start_idx=None,
    )
    adopted = invocation_args[COMMAND_SKILL_SNAPSHOT_ARG]

    assert [item.record.id for item in adopted.activations] == [selection.id]
    conversation._append_tool_call_result_event(
        {
            "id": "skill-call",
            "event_id": "skill-event",
            "name": "skill_read",
            "args": {},
        },
        result,
        0,
    )
    assert "_activated_skill" not in conversation.session.events[-1].meta["extra"]


def test_explicit_name_is_host_activated_before_the_model(tmp_path: Path) -> None:
    conversation, llm, _, selection = _conversation(tmp_path)

    events = list(conversation.stream("请用 test-skill 处理这个任务"))

    assert len(llm.received) == 1
    assert any("FIRST VERSION" in item.get("content", "") for item in llm.received[0])
    activation = next(event for event in events if isinstance(event, SkillActivationStatus))
    assert activation.status == "activated"
    assert activation.skill_id == selection.id
    assert not any(event.type == "tool_call_result" for event in events)


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

    events = list(conversation.stream("continue", skill_selections=(SkillSelection("missing"),)))

    assert any("<skill_load_error>" in item["content"] for item in llm.received[0])
    skill_event = next(
        event for event in conversation.session.events if event.type == "skill_context"
    )
    assert skill_event.payload["error_code"] == "skill_not_found"
    status = next(event for event in events if isinstance(event, SkillActivationStatus))
    assert status.status == "error"
    assert status.skill_id == "missing"
    assert status.error_code == "skill_not_found"


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
