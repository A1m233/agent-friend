"""035：编辑并重发最后一条 user query。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from agent.sessions import Event

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
class _ScriptedLLMClient:
    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    turn_idx: int = 0
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:  # pragma: no cover
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(messages)
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


class _FailingLLMClient:
    context_window = 128000

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:  # pragma: no cover
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        raise RuntimeError("llm init failed")
        yield  # pragma: no cover


def _conversation(
    tmp_path: Path,
    *,
    session: Session | None = None,
    store: JsonlSessionStore | None = None,
    llm: LLMClient,
) -> Conversation:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID
    store = store or JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = session or Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=persona_id,
    )
    if not (tmp_path / "sessions" / f"{session.session_id}.jsonl").exists():
        store.create(session)
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    def builder_factory(pid: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=llm,
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
    )


def _event(type_: str, payload: dict[str, Any]) -> Event:
    return Event(
        type=type_,  # type: ignore[arg-type]
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload=payload,
    )


def _append(store: JsonlSessionStore, session: Session, event: Event) -> None:
    store.append_event(session.session_id, event)
    session.append(event)


def test_session_active_events_hide_rewritten_tail() -> None:
    session = Session.new(title="t", persona="default", model="m")
    old_user = _event("user_message", {"content": "old"})
    old_assistant = _event("assistant_message", {"content": "old answer", "partial": False})
    marker = Event(
        type="turn_rewrite",
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload={
            "reason": "edit_resend_latest",
            "target_user_uuid": old_user.uuid,
            "inactive_event_uuids": [old_user.uuid, old_assistant.uuid],
            "replacement_text_sha256": "x",
        },
    )
    new_user = _event("user_message", {"content": "new"})
    new_assistant = _event("assistant_message", {"content": "new answer", "partial": False})
    session.events.extend([old_user, old_assistant, marker, new_user, new_assistant])

    assert [ev.uuid for ev in session.active_events] == [
        session.session_id,
        new_user.uuid,
        new_assistant.uuid,
    ]
    assert [(m.role, m.content) for m in session.messages] == [
        ("user", "new"),
        ("assistant", "new answer"),
    ]


def test_edit_resend_latest_uses_prefix_context_and_appends_rewrite(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [LLMTextDelta(text="old answer"), LLMTurnDone(stop_reason="end_turn")],
            [LLMTextDelta(text="new answer"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _conversation(tmp_path, llm=cast(LLMClient, fake))

    list(conv.stream("old question"))
    list(conv.edit_resend_latest("new question", expected_user_content="old question"))

    assert [ev.type for ev in conv.session.events].count("turn_rewrite") == 1
    assert [(m.role, m.content) for m in conv.session.messages] == [
        ("user", "new question"),
        ("assistant", "new answer"),
    ]

    second_prompt = "\n".join(str(m.get("content", "")) for m in fake.received[1])
    assert "new question" in second_prompt
    assert "old question" not in second_prompt
    assert "old answer" not in second_prompt


def test_edit_resend_latest_first_llm_failure_writes_no_marker(tmp_path: Path) -> None:
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    store.create(session)
    old_user = _event("user_message", {"content": "old question"})
    old_assistant = _event("assistant_message", {"content": "old answer", "partial": False})
    _append(store, session, old_user)
    _append(store, session, old_assistant)
    conv = _conversation(
        tmp_path,
        session=session,
        store=store,
        llm=cast(LLMClient, _FailingLLMClient()),
    )

    with pytest.raises(RuntimeError, match="llm init failed"):
        list(conv.edit_resend_latest("new question", expected_user_content="old question"))

    assert [ev.type for ev in conv.session.events] == [
        "session_meta",
        "user_message",
        "assistant_message",
    ]
    assert [(m.role, m.content) for m in conv.session.messages] == [
        ("user", "old question"),
        ("assistant", "old answer"),
    ]
