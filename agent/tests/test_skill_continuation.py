"""Skill capability recovery through the durable same-task continuation entry."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from agent.skills import SkillSelection

from agent import Conversation, JsonlSessionStore, SkillActivationStatus
from llm_providers import LLMNetworkError, LLMStreamEvent, LLMTextDelta, LLMTurnDone, ModelRequest

from .test_skill_conversation import (
    _conversation,
    _RecordingLLM,
    _reopen_conversation,
    _SkillCallingLLM,
    _write_skill,
)


class _UnavailableLLM(_RecordingLLM):
    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        raise LLMNetworkError("provider unavailable before first output")


class _ReadThenFinishLLM(_SkillCallingLLM):
    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        if not self.received:
            yield from super().stream(request)
        else:
            self.received.append(request.messages)
            yield LLMTextDelta("done")
            yield LLMTurnDone(stop_reason="end_turn")


def _fail_task(conversation: Conversation, selections: tuple[SkillSelection, ...] = ()) -> None:
    conversation._llm_client = _UnavailableLLM()
    with pytest.raises(LLMNetworkError):
        list(
            conversation.stream(
                "run daily",
                user_event_id="original-user",
                run_id="failed-run",
                skill_selections=selections,
                request_source={"kind": "scheduled_task", "task_id": "daily"},
            )
        )
    conversation.append_run_error_event(
        code="upstream_transient",
        message="provider unavailable",
        recoverable=True,
        source="test",
        run_id="failed-run",
    )


@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_resume_restores_skill_read_and_live_binding(
    tmp_path: Path, cold: bool, explicit: bool
) -> None:
    conversation, _, service, selection = _conversation(tmp_path)
    if explicit:
        agents = service.personal_root / "test-skill" / "agents"
        agents.mkdir()
        (agents / "openai.yaml").write_text(
            "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
        )
        service.refresh()
    _fail_task(conversation, (selection,) if explicit else ())
    original_events = list(conversation.session.events)
    if cold:
        store = JsonlSessionStore(tmp_path / "sessions")
        conversation, _ = _reopen_conversation(
            tmp_path,
            session=store.load(conversation.session.session_id),
            store=store,
            skill_service=service,
        )

    bindings: list[str] = []

    class ResumingLLM(_ReadThenFinishLLM):
        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            if self.received:
                context = conversation._active_tool_run_context
                assert context is not None
                activation = context.skill_activations[selection.id]
                assert activation.resource_root.is_dir()
                assert activation.invocation_kind == ("caller" if explicit else "implicit")
                bindings.append(activation.contents)
            yield from super().stream(request)

    llm = ResumingLLM(selection.id)
    conversation._llm_client = llm
    list(conversation.continue_after_tool_error(run_id="resumed", source_run_id="failed-run"))

    results = [e for e in conversation.session.events if e.type == "tool_call_result"]
    assert len(results) == 1
    assert results[0].payload["is_error"] is False
    assert bindings and "FIRST VERSION" in bindings[0]
    assert conversation.session.events[: len(original_events)] == original_events
    users = [e for e in conversation.session.events if e.type == "user_message"]
    assert [e.uuid for e in users] == ["original-user"]
    assert users[0].payload["request_source"]["task_id"] == "daily"


def test_resume_refreshes_catalog_without_inventing_explicit_selection(tmp_path: Path) -> None:
    conversation, _, service, _ = _conversation(tmp_path)
    _fail_task(conversation)
    # The new Skill name occurs in the original request, but was not a caller selection.
    package = _write_skill(service.personal_root, "daily", "NEW EXPLICIT ONLY").parent
    agents = package / "agents"
    agents.mkdir()
    (agents / "openai.yaml").write_text(
        "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
    )
    _write_skill(service.personal_root, "new-visible", "NEW VISIBLE BODY")
    snapshot = service.refresh()
    restricted = next(e for e in snapshot.entries if e.name == "daily")

    llm = _ReadThenFinishLLM(restricted.id)
    conversation._llm_client = llm
    list(conversation.continue_after_tool_error(run_id="resumed"))

    catalogs = [m["content"] for m in llm.received[0] if "<skills_catalog>" in m["content"]]
    assert "new-visible" in catalogs[-1]
    result = next(e for e in conversation.session.events if e.type == "tool_call_result")
    assert result.payload["is_error"] is True
    assert result.payload["error_code"] == "implicit_invocation_disabled"
    assert not any(e.type == "skill_context" for e in conversation.session.events)


def test_resume_reports_changed_explicit_skill_without_activating_new_version(
    tmp_path: Path,
) -> None:
    conversation, _, service, selection = _conversation(tmp_path)
    _fail_task(conversation, (selection,))
    _write_skill(service.personal_root, "test-skill", "SECOND VERSION")
    service.refresh()
    llm = _RecordingLLM()
    conversation._llm_client = llm

    events = list(conversation.continue_after_tool_error(run_id="resumed"))

    status = next(e for e in events if isinstance(e, SkillActivationStatus))
    assert status.error_code == "skill_version_mismatch"
    assert any("skill_version_mismatch" in m["content"] for m in llm.received[0])
    assert not any("SECOND VERSION" in m["content"] for m in llm.received[0])


def test_completed_idempotent_replay_does_not_reactivate_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, llm, service, selection = _conversation(tmp_path)
    list(conversation.stream("first", user_event_id="stable", skill_selections=(selection,)))

    def unexpected_activation(*args: object, **kwargs: object) -> None:
        pytest.fail("completed replay must not capture a new Skill package")

    monkeypatch.setattr(service, "begin_turn", unexpected_activation)
    list(conversation.stream("first", user_event_id="stable", skill_selections=(selection,)))
    assert len(llm.received) == 1
