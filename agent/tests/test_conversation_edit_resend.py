"""035：编辑并重发最后一条 user query。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

import pytest
from agent.sessions import Event

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    AgentError,
    AttachmentRef,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    Message,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
    TextDelta,
    ToolRegistry,
    ToolResult,
    ToolRunPolicy,
    TurnDone,
)
from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
)


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


class _EchoTool:
    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "test echo"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(text=str(args.get("message", "")))


def _conversation(
    tmp_path: Path,
    *,
    session: Session | None = None,
    store: JsonlSessionStore | None = None,
    llm: LLMClient,
    llm_factory: Any | None = None,
    tool_registry: ToolRegistry | None = None,
    tool_run_policy: ToolRunPolicy | None = None,
    message_renderer: Any | None = None,
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
        llm_client_factory=llm_factory,
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        tool_registry=tool_registry,
        tool_run_policy=tool_run_policy,
        message_renderer=message_renderer,
    )


def test_edit_resend_preserves_later_model_change(tmp_path: Path) -> None:
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-v4-flash",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    store.create(session)
    _append(store, session, _event("user_message", {"content": "old question"}))
    _append(
        store,
        session,
        _event("assistant_message", {"content": "old answer", "partial": False}),
    )
    _append(
        store,
        session,
        _event(
            "model_change",
            {"from": "deepseek/deepseek-v4-flash", "to": "kimi/kimi-k2.6"},
        ),
    )
    llm = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="new answer"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _conversation(tmp_path, session=session, store=store, llm=cast(LLMClient, llm))

    list(conv.edit_resend_latest("new question", expected_user_content="old question"))

    assert session.current_model == "kimi/kimi-k2.6"
    active_changes = [event for event in session.active_events if event.type == "model_change"]
    assert len(active_changes) == 1
    assistants = [event for event in session.active_events if event.type == "assistant_message"]
    assert assistants[-1].meta["model"] == "kimi/kimi-k2.6"


def _event(
    type_: str,
    payload: dict[str, Any],
    *,
    meta: dict[str, Any] | None = None,
) -> Event:
    return Event(
        type=type_,  # type: ignore[arg-type]
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload=payload,
        meta=meta or {},
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


def test_content_policy_failure_sanitizes_only_the_last_model_call(
    tmp_path: Path,
) -> None:
    run_id = "rejected-run"
    session = Session.new(title="t", persona="default", model="m")
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    store.create(session)
    events = [
        _event("user_message", {"content": "rejected question"}, meta={"run_id": run_id}),
        _event(
            "assistant_message",
            {"content": "first search", "partial": False},
            meta={"run_id": run_id},
        ),
        _event(
            "tool_call_request",
            {
                "tool_call_id": "safe-call",
                "tool_name": "web_search",
                "args": {"query": "safe"},
            },
            meta={"run_id": run_id},
        ),
        _event(
            "tool_call_result",
            {
                "tool_call_id": "safe-call",
                "tool_name": "web_search",
                "content": "safe search payload",
                "is_error": False,
            },
            meta={"run_id": run_id},
        ),
        _event(
            "assistant_message",
            {"content": "second search", "partial": False},
            meta={"run_id": run_id},
        ),
        _event(
            "tool_call_request",
            {
                "tool_call_id": "rejected-call",
                "tool_name": "web_search",
                "args": {"query": "risky"},
            },
            meta={"run_id": run_id},
        ),
        _event(
            "tool_call_result",
            {
                "tool_call_id": "rejected-call",
                "tool_name": "web_search",
                "content": "rejected search payload",
                "is_error": False,
            },
            meta={"run_id": run_id},
        ),
        _event(
            "run_error",
            {
                "code": "content_policy",
                "message": "blocked",
                "run_id": run_id,
                "sanitize_failed_model_call": True,
            },
        ),
    ]
    for event in events:
        _append(store, session, event)

    assert any(message.content == "rejected search payload" for message in session.messages)
    assert [(message.role, message.content) for message in session.llm_messages] == [
        ("user", "rejected question"),
        ("assistant", "first search"),
        ("tool", "safe search payload"),
        ("assistant", "上一轮因模型安全限制未能完成，受限内容不再重放。"),
    ]

    fake = _ScriptedLLMClient(script=[[LLMTurnDone(stop_reason="end_turn")]])
    conv = _conversation(tmp_path, session=session, store=store, llm=cast(LLMClient, fake))
    list(conv.stream("fresh question"))

    sent = "\n".join(str(message.get("content", "")) for message in fake.received[0])
    assert "fresh question" in sent
    assert "rejected question" in sent
    assert "safe search payload" in sent
    assert "second search" not in sent
    assert "rejected search payload" not in sent
    assert "受限内容不再重放" in sent


def test_content_policy_failure_on_first_model_call_does_not_replay_user_input() -> None:
    run_id = "first-call-rejected"
    session = Session.new(title="t", persona="default", model="m")
    session.events.extend(
        [
            _event(
                "user_message",
                {"content": "blocked raw user input"},
                meta={"run_id": run_id},
            ),
            _event(
                "run_error",
                {
                    "code": "content_policy",
                    "message": "blocked",
                    "run_id": run_id,
                    "sanitize_failed_model_call": True,
                },
            ),
        ]
    )

    assert [(message.role, message.content) for message in session.messages] == [
        ("user", "blocked raw user input")
    ]
    assert [(message.role, message.content) for message in session.llm_messages] == [
        ("assistant", "上一轮因模型安全限制未能完成，受限内容不再重放。")
    ]


def test_legacy_bad_request_after_tool_result_sanitizes_last_transaction() -> None:
    run_id = "legacy-bad-request"
    session = Session.new(title="t", persona="default", model="m")
    session.events.extend(
        [
            _event("user_message", {"content": "question"}, meta={"run_id": run_id}),
            _event(
                "assistant_message",
                {"content": "searching", "partial": False},
                meta={"run_id": run_id},
            ),
            _event(
                "tool_call_result",
                {
                    "tool_call_id": "legacy-call",
                    "tool_name": "web_search",
                    "content": "legacy rejected payload",
                    "is_error": False,
                },
                meta={"run_id": run_id},
            ),
            _event(
                "run_error",
                {"code": "bad_request", "message": "bad request", "run_id": run_id},
            ),
        ]
    )

    assert [(message.role, message.content) for message in session.llm_messages] == [
        ("user", "question"),
        ("assistant", "上一轮工具结果未被模型接受，相关内容不再重放。"),
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


def test_edit_resend_uses_the_same_tool_budget_stop_semantics(tmp_path: Path) -> None:
    tool_use: list[LLMStreamEvent] = [
        LLMToolCallDelta(
            index=0,
            tool_call_id="edit-call",
            tool_name="echo",
            args_json_delta='{"message":"evidence"}',
        ),
        LLMTurnDone(stop_reason="tool_use"),
    ]
    fake = _ScriptedLLMClient(
        script=[
            [LLMTextDelta(text="old answer"), LLMTurnDone(stop_reason="end_turn")],
            tool_use,
            tool_use,
            [LLMTextDelta(text="bounded answer"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _conversation(
        tmp_path,
        llm=cast(LLMClient, fake),
        tool_registry=ToolRegistry([_EchoTool()]),
        tool_run_policy=ToolRunPolicy(max_model_continuations=1),
    )
    list(conv.stream("old question"))
    events = list(conv.edit_resend_latest("new question", expected_user_content="old question"))

    done = next(event for event in events if isinstance(event, TurnDone))
    assert done.stop_reason == "budget_exhausted"
    assert done.budget_dimension == "model_continuations"
    assert done.total_tool_calls == 2


def test_edit_resend_latest_preserves_attachments(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [LLMTurnDone(stop_reason="end_turn")],
            [LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _conversation(tmp_path, llm=cast(LLMClient, fake))
    attachment = AttachmentRef(
        asset_id="a" * 64,
        display_name="report.pdf",
        kind="document",
        media_type="application/pdf",
        size_bytes=10,
        sha256="a" * 64,
    )

    list(conv.stream("read", attachments=[attachment]))
    list(conv.edit_resend_latest("read carefully", expected_user_content="read"))

    user = next(message for message in conv.session.messages if message.role == "user")
    assert user.content == "read carefully"
    assert user.attachments == [attachment]


def test_edit_resend_latest_replaces_attachments_and_allows_attachment_only(
    tmp_path: Path,
) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [LLMTurnDone(stop_reason="end_turn")],
            [LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _conversation(tmp_path, llm=cast(LLMClient, fake))
    original = AttachmentRef(
        asset_id="c" * 64,
        display_name="original.txt",
        kind="document",
        media_type="text/plain",
        size_bytes=10,
        sha256="c" * 64,
    )
    replacement = AttachmentRef(
        asset_id="d" * 64,
        display_name="replacement.txt",
        kind="document",
        media_type="text/plain",
        size_bytes=20,
        sha256="d" * 64,
    )

    list(conv.stream("old", attachments=[original]))
    target_uuid = next(
        event.uuid for event in reversed(conv.session.active_events) if event.type == "user_message"
    )
    list(
        conv.edit_resend_latest(
            "",
            attachments=[replacement],
            expected_user_uuid=target_uuid,
            expected_user_content="old",
        )
    )

    user = next(message for message in conv.session.messages if message.role == "user")
    assert user.content == ""
    assert user.attachments == [replacement]


def test_edit_resend_latest_explicit_empty_attachments_removes_original(
    tmp_path: Path,
) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [LLMTurnDone(stop_reason="end_turn")],
            [LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _conversation(tmp_path, llm=cast(LLMClient, fake))
    original = AttachmentRef(
        asset_id="e" * 64,
        display_name="original.pdf",
        kind="document",
        media_type="application/pdf",
        size_bytes=10,
        sha256="e" * 64,
    )

    list(conv.stream("old", attachments=[original]))
    list(
        conv.edit_resend_latest(
            "text only",
            attachments=[],
            expected_user_content="old",
        )
    )

    user = next(message for message in conv.session.messages if message.role == "user")
    assert user.content == "text only"
    assert user.attachments == []


def test_edit_resend_latest_rejects_stale_user_uuid(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(script=[[LLMTurnDone(stop_reason="end_turn")]])
    conv = _conversation(tmp_path, llm=cast(LLMClient, fake))
    list(conv.stream("old"))

    with pytest.raises(AgentError, match="最后一条用户消息已变化"):
        list(
            conv.edit_resend_latest(
                "new",
                expected_user_uuid="stale-user-uuid",
                expected_user_content="old",
            )
        )


def test_edit_resend_latest_reinjects_image_evidence_boundary(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [LLMTurnDone(stop_reason="end_turn")],
            [LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _conversation(tmp_path, llm=cast(LLMClient, fake))
    image = AttachmentRef(
        asset_id="b" * 64,
        display_name="screenshot.png",
        kind="image",
        media_type="image/png",
        size_bytes=10,
        sha256="b" * 64,
    )

    list(conv.stream("look", attachments=[image]))
    list(conv.edit_resend_latest("look carefully", expected_user_content="look"))

    assert len(fake.received) == 2
    for messages in fake.received:
        assert any(
            message.get("role") == "system"
            and "<image_evidence_boundary>" in str(message.get("content", ""))
            for message in messages
        )


def test_stream_first_llm_failure_persists_user_turn(tmp_path: Path) -> None:
    conv = _conversation(
        tmp_path,
        llm=cast(LLMClient, _FailingLLMClient()),
    )

    with pytest.raises(RuntimeError, match="llm init failed"):
        list(conv.stream("question before failure"))

    assert [ev.type for ev in conv.session.events] == [
        "session_meta",
        "user_message",
    ]
    assert [(m.role, m.content) for m in conv.session.messages] == [
        ("user", "question before failure"),
    ]


def test_stream_context_render_failure_persists_user_before_failure(tmp_path: Path) -> None:
    def fail_renderer(message: Message, client: LLMClient) -> dict[str, Any]:
        del client
        if message.attachments:
            raise RuntimeError("attachment render failed")
        return message.to_openai()

    conv = _conversation(
        tmp_path,
        llm=cast(LLMClient, _ScriptedLLMClient()),
        message_renderer=fail_renderer,
    )
    attachment = AttachmentRef(
        asset_id="a" * 64,
        display_name="evidence.png",
        kind="image",
        media_type="image/png",
        size_bytes=8,
        sha256="a" * 64,
    )

    with pytest.raises(RuntimeError, match="attachment render failed"):
        list(
            conv.stream(
                "question survives context failure",
                attachments=[attachment],
                user_event_id="stable-user-id",
            )
        )

    assert [ev.type for ev in conv.session.events] == [
        "session_meta",
        "user_message",
    ]
    user = conv.session.events[-1]
    assert user.uuid == "stable-user-id"
    assert user.payload == {
        "content": "question survives context failure",
        "attachments": [attachment.to_dict()],
    }


def test_stream_commits_user_before_first_cancel_check(tmp_path: Path) -> None:
    from agent.runtime.cancellation import CancellationToken, CancelReason, RunCancelled

    token = CancellationToken()
    assert token.cancel(CancelReason.EXPLICIT_REQUEST)
    conv = _conversation(
        tmp_path,
        llm=cast(LLMClient, _ScriptedLLMClient()),
    )

    with pytest.raises(RunCancelled):
        list(
            conv.stream(
                "keep this cancelled question",
                user_event_id="cancelled-user-id",
                cancel_check=token.raise_if_cancelled,
            )
        )

    assert [ev.type for ev in conv.session.events] == [
        "session_meta",
        "user_message",
        "assistant_message",
    ]
    assert conv.session.events[1].uuid == "cancelled-user-id"
    assert conv.session.events[1].payload == {"content": "keep this cancelled question"}
    assert conv.session.events[2].payload == {
        "content": "",
        "partial": True,
        "interruption_reason": "explicit_request",
    }


def test_stream_persist_before_build_does_not_duplicate_model_user_input(
    tmp_path: Path,
) -> None:
    fake = _ScriptedLLMClient(script=[[LLMTurnDone(stop_reason="end_turn")]])
    conv = _conversation(tmp_path, llm=cast(LLMClient, fake))

    list(conv.stream("exactly once"))

    matching = [
        message
        for message in fake.received[0]
        if message.get("role") == "user" and message.get("content") == "exactly once"
    ]
    assert len(matching) == 1


def test_edit_resend_latest_first_llm_failure_persists_rewrite_and_user(
    tmp_path: Path,
) -> None:
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
        "turn_rewrite",
        "user_message",
    ]
    assert [(m.role, m.content) for m in conv.session.messages] == [
        ("user", "new question"),
    ]


def test_edit_resend_latest_commits_new_branch_before_context_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    store.create(session)
    _append(store, session, _event("user_message", {"content": "old question"}))
    _append(
        store,
        session,
        _event("assistant_message", {"content": "old answer", "partial": False}),
    )
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="new answer"), LLMTurnDone(stop_reason="stop")]]
    )
    conv = _conversation(
        tmp_path,
        session=session,
        store=store,
        llm=cast(LLMClient, fake),
    )
    original_build = conv._build_openai_messages_first_turn

    def assert_committed_before_build(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        assert [event.type for event in conv.session.events][-2:] == [
            "turn_rewrite",
            "user_message",
        ]
        assert [(message.role, message.content) for message in conv.session.messages] == [
            ("user", "new question"),
        ]
        return original_build(*args, **kwargs)

    monkeypatch.setattr(
        conv,
        "_build_openai_messages_first_turn",
        assert_committed_before_build,
    )

    list(conv.edit_resend_latest("new question", expected_user_content="old question"))

    assert [(message.role, message.content) for message in conv.session.messages] == [
        ("user", "new question"),
        ("assistant", "new answer"),
    ]


def test_user_event_id_retry_rejects_different_content(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(script=[[LLMTextDelta(text="ok"), LLMTurnDone(stop_reason="stop")]])
    conv = _conversation(tmp_path, llm=cast(LLMClient, fake))
    list(conv.stream("first", user_event_id="stable-message-id"))
    replay = list(conv.stream("first", user_event_id="stable-message-id"))

    assert any(isinstance(event, TextDelta) and event.text == "ok" for event in replay)
    assert len(fake.received) == 1

    with pytest.raises(ValueError, match="user_event_id_conflict"):
        list(conv.stream("different", user_event_id="stable-message-id"))
