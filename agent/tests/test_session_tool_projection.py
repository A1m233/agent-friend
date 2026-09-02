from __future__ import annotations

from datetime import UTC, datetime

from agent.sessions.model_history import SessionModelHistory

from agent import Event, Message, Session


def _event(
    type_: str,
    uuid: str,
    payload: dict[str, object],
    *,
    run_id: str = "run-1",
) -> Event:
    return Event(
        type=type_,  # type: ignore[arg-type]
        uuid=uuid,
        ts=datetime.now(UTC),
        payload=payload,
        meta={"run_id": run_id},
    )


def test_llm_messages_replaces_unclosed_tool_only_assistant() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.append(_event("user_message", "u", {"content": "hello"}))
    session.append(_event("assistant_message", "a", {"content": "", "partial": False}))
    session.append(
        _event(
            "tool_call_request",
            "req",
            {"tool_call_id": "call-1", "tool_name": "search", "args": {}},
        )
    )

    projected = session.llm_messages

    assert [message.role for message in projected] == ["user", "assistant"]
    assert projected[-1].content == "上一轮工具调用未完成，可以继续当前对话。"
    assert projected[-1].meta["synthetic"] == "incomplete_tool_transaction"
    assert "tool_calls" not in projected[-1].meta
    assert [event.type for event in session.events][-2:] == [
        "assistant_message",
        "tool_call_request",
    ]


def test_llm_messages_ignores_legacy_persisted_model_context() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.append(_event("user_message", "u", {"content": "hello"}))
    session.append(
        _event(
            "model_context",
            "legacy-clock",
            {
                "kind": "runtime_context",
                "content": '<runtime_now at="2026-07-28T22:04:13+08:00" />',
            },
        )
    )
    session.append(_event("assistant_message", "a", {"content": "hi", "partial": False}))

    assert [(message.role, message.content) for message in session.llm_messages] == [
        ("user", "hello"),
        ("assistant", "hi"),
    ]
    assert any(event.type == "model_context" for event in session.events)


def test_llm_messages_keeps_closed_sibling_and_removes_unmatched_request() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.append(_event("user_message", "u", {"content": "hello"}))
    session.append(_event("assistant_message", "a", {"content": "", "partial": False}))
    session.append(
        _event(
            "tool_call_request",
            "req-1",
            {"tool_call_id": "call-1", "tool_name": "search", "args": {"q": "a"}},
        )
    )
    session.append(
        _event(
            "tool_call_request",
            "req-2",
            {"tool_call_id": "call-2", "tool_name": "search", "args": {"q": "b"}},
        )
    )
    session.append(
        _event(
            "tool_call_result",
            "res-1",
            {
                "tool_call_id": "call-1",
                "tool_name": "search",
                "content": "result-a",
                "is_error": False,
            },
        )
    )

    projected = session.llm_messages

    assistant = projected[1]
    assert assistant.role == "assistant"
    assert [call["id"] for call in assistant.meta["tool_calls"]] == ["call-1"]
    assert projected[2].role == "tool"
    assert projected[2].meta["tool_call_id"] == "call-1"


def test_closed_tool_request_projects_protocol_assistant_without_persisted_empty_event() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.append(_event("user_message", "u", {"content": "hello"}))
    session.append(
        _event(
            "tool_call_request",
            "req",
            {"tool_call_id": "call-1", "tool_name": "search", "args": {"q": "a"}},
        )
    )
    session.append(
        _event(
            "tool_call_result",
            "res",
            {
                "tool_call_id": "call-1",
                "tool_name": "search",
                "content": "result-a",
                "is_error": False,
            },
        )
    )

    projected = session.llm_messages

    assert [message.role for message in projected] == ["user", "assistant", "tool"]
    assert projected[1].content == ""
    assert projected[1].meta["synthetic"] == "tool_call_container"
    assert projected[1].meta["tool_calls"] == [
        {"id": "call-1", "name": "search", "args": {"q": "a"}},
    ]
    assert not any(event.type == "assistant_message" for event in session.events)


def test_llm_messages_relocates_late_recovered_result_next_to_original_call() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.events.extend(
        [
            _event("user_message", "old-user", {"content": "old"}, run_id="old-run"),
            _event(
                "assistant_message",
                "old-assistant",
                {"content": "", "partial": False},
                run_id="old-run",
            ),
            _event(
                "tool_call_request",
                "old-request",
                {
                    "tool_call_id": "provider-call",
                    "event_tool_call_id": "event-call",
                    "tool_name": "exec_command",
                    "args": {},
                },
                run_id="old-run",
            ),
            _event("user_message", "later-user", {"content": "later"}, run_id="later-run"),
            _event(
                "assistant_message",
                "later-assistant",
                {"content": "later answer", "partial": False},
                run_id="later-run",
            ),
            _event(
                "tool_call_result",
                "late-result",
                {
                    "tool_call_id": "provider-call",
                    "event_tool_call_id": "event-call",
                    "tool_name": "exec_command",
                    "content": "interrupted",
                    "is_error": True,
                    "result_origin": "host_reconciliation",
                },
                run_id="old-run",
            ),
        ]
    )

    projected = session.llm_messages
    SessionModelHistory.validate_provider_tool_history(
        [message.to_openai() for message in projected]
    )

    assert [message.uuid for message in projected] == [
        "old-user",
        "old-assistant",
        "late-result",
        "later-user",
        "later-assistant",
    ]


def test_llm_messages_keeps_first_result_for_one_event_transaction() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.events.extend(
        [
            _event("user_message", "user", {"content": "run"}),
            _event("assistant_message", "assistant", {"content": "", "partial": False}),
            _event(
                "tool_call_request",
                "request",
                {
                    "tool_call_id": "provider-call",
                    "event_tool_call_id": "event-call",
                    "tool_name": "exec_command",
                    "args": {},
                },
            ),
            _event(
                "tool_call_result",
                "first-result",
                {
                    "tool_call_id": "provider-call",
                    "event_tool_call_id": "event-call",
                    "tool_name": "exec_command",
                    "content": "first",
                    "is_error": True,
                },
            ),
            _event(
                "tool_call_result",
                "duplicate-result",
                {
                    "tool_call_id": "provider-call",
                    "event_tool_call_id": "event-call",
                    "tool_name": "exec_command",
                    "content": "duplicate",
                    "is_error": True,
                },
            ),
        ]
    )

    projected = session.llm_messages
    SessionModelHistory.validate_provider_tool_history(
        [message.to_openai() for message in projected]
    )

    assert [message.uuid for message in projected if message.role == "tool"] == ["first-result"]


def test_llm_messages_orders_late_results_by_their_original_request_group() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.events.extend(
        [
            _event("user_message", "user", {"content": "run"}, run_id="run"),
            _event(
                "assistant_message",
                "assistant",
                {"content": "", "partial": False},
                run_id="run",
            ),
            _event(
                "tool_call_request",
                "request-1",
                {
                    "tool_call_id": "provider-call-1",
                    "event_tool_call_id": "event-call-1",
                    "tool_name": "exec_command",
                    "args": {},
                },
                run_id="run",
            ),
            _event(
                "tool_call_request",
                "request-2",
                {
                    "tool_call_id": "provider-call-2",
                    "event_tool_call_id": "event-call-2",
                    "tool_name": "exec_command",
                    "args": {},
                },
                run_id="run",
            ),
            _event("user_message", "later-user", {"content": "later"}, run_id="later"),
            _event(
                "tool_call_result",
                "result-2",
                {
                    "tool_call_id": "provider-call-2",
                    "event_tool_call_id": "event-call-2",
                    "tool_name": "exec_command",
                    "content": "second",
                    "is_error": True,
                },
                run_id="run",
            ),
            _event(
                "tool_call_result",
                "result-1",
                {
                    "tool_call_id": "provider-call-1",
                    "event_tool_call_id": "event-call-1",
                    "tool_name": "exec_command",
                    "content": "first",
                    "is_error": True,
                },
                run_id="run",
            ),
        ]
    )

    projected = session.llm_messages
    SessionModelHistory.validate_provider_tool_history(
        [message.to_openai() for message in projected]
    )

    assert [message.uuid for message in projected] == [
        "user",
        "assistant",
        "result-1",
        "result-2",
        "later-user",
    ]


def test_llm_messages_disambiguates_reused_provider_ids_across_runs() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.events.extend(
        [
            _event("user_message", "user-1", {"content": "first"}, run_id="run-1"),
            _event(
                "assistant_message",
                "assistant-1",
                {"content": "", "partial": False},
                run_id="run-1",
            ),
            _event(
                "tool_call_request",
                "request-1",
                {
                    "tool_call_id": "reused-provider-call",
                    "event_tool_call_id": "event-call-1",
                    "tool_name": "exec_command",
                    "args": {},
                },
                run_id="run-1",
            ),
            _event("user_message", "user-2", {"content": "second"}, run_id="run-2"),
            _event(
                "assistant_message",
                "assistant-2",
                {"content": "", "partial": False},
                run_id="run-2",
            ),
            _event(
                "tool_call_request",
                "request-2",
                {
                    "tool_call_id": "reused-provider-call",
                    "event_tool_call_id": "event-call-2",
                    "tool_name": "exec_command",
                    "args": {},
                },
                run_id="run-2",
            ),
            _event(
                "tool_call_result",
                "result-2",
                {
                    "tool_call_id": "reused-provider-call",
                    "event_tool_call_id": "event-call-2",
                    "tool_name": "exec_command",
                    "content": "second result",
                    "is_error": False,
                },
                run_id="run-2",
            ),
            _event(
                "tool_call_result",
                "late-result-1",
                {
                    "tool_call_id": "reused-provider-call",
                    "event_tool_call_id": "event-call-1",
                    "tool_name": "exec_command",
                    "content": "first interrupted",
                    "is_error": True,
                },
                run_id="run-1",
            ),
        ]
    )

    projected = session.llm_messages
    SessionModelHistory.validate_provider_tool_history(
        [message.to_openai() for message in projected]
    )

    assert [message.uuid for message in projected] == [
        "user-1",
        "assistant-1",
        "late-result-1",
        "user-2",
        "assistant-2",
        "result-2",
    ]


def test_insert_tool_result_moves_existing_message_idempotently() -> None:
    history = SessionModelHistory(
        items=[
            Message(
                role="assistant",
                content="",
                uuid="assistant",
                meta={
                    "provider_run_id": "run",
                    "tool_calls": [{"id": "call", "name": "exec_command", "args": {}}],
                },
            ),
            Message(role="user", content="later", uuid="later-user"),
            Message(
                role="tool",
                content="interrupted",
                uuid="result",
                meta={
                    "provider_run_id": "run",
                    "tool_call_id": "call",
                    "tool_name": "exec_command",
                    "is_error": True,
                },
            ),
        ]
    )

    history.insert_tool_result_after_call(
        content="interrupted",
        tool_call_id="call",
        tool_name="exec_command",
        timestamp=history.items[-1].timestamp,
        event_uuid="result",
        is_error=True,
        run_id="run",
    )
    first_generation = history.generation
    history.insert_tool_result_after_call(
        content="interrupted",
        tool_call_id="call",
        tool_name="exec_command",
        timestamp=history.items[1].timestamp,
        event_uuid="result",
        is_error=True,
        run_id="run",
    )

    assert [message.uuid for message in history.items] == ["assistant", "result", "later-user"]
    assert history.generation == first_generation
    SessionModelHistory.validate_provider_tool_history(
        [message.to_openai() for message in history.items]
    )


def test_provider_reasoning_state_only_enters_model_projection() -> None:
    session = Session.new(title="t", persona="default", model="kimi/kimi-k2.7-code")
    session.append(_event("user_message", "u", {"content": "hello"}))
    session.append(_event("assistant_message", "a", {"content": "visible", "partial": False}))
    session.append(
        _event(
            "provider_reasoning_state",
            "reasoning",
            {
                "provider": "kimi",
                "model": "kimi/kimi-k2.7-code",
                "format": "reasoning_content",
                "replayScope": "conversation",
                "data": "private",
            },
        )
    )

    assert "provider_reasoning_state" not in session.messages[-1].meta
    assert session.llm_messages[-1].meta["provider_reasoning_state"]["data"] == "private"


def test_provider_reasoning_state_attaches_to_synthetic_tool_container() -> None:
    session = Session.new(title="t", persona="default", model="kimi/kimi-k2.7-code")
    session.append(_event("user_message", "u", {"content": "hello"}))
    session.append(
        _event(
            "provider_reasoning_state",
            "reasoning",
            {
                "provider": "kimi",
                "model": "kimi/kimi-k2.7-code",
                "format": "reasoning_content",
                "replayScope": "conversation",
                "data": "private",
            },
        )
    )
    session.append(
        _event(
            "tool_call_request",
            "req",
            {"tool_call_id": "call-1", "tool_name": "search", "args": {"q": "a"}},
        )
    )
    session.append(
        _event(
            "tool_call_result",
            "res",
            {
                "tool_call_id": "call-1",
                "tool_name": "search",
                "content": "result-a",
                "is_error": False,
            },
        )
    )

    assistant = session.llm_messages[1]
    assert assistant.meta["provider_reasoning_state"]["data"] == "private"
    assert assistant.meta["tool_calls"][0]["id"] == "call-1"


def test_llm_messages_replaces_partial_assistant_before_new_attachment_turn() -> None:
    session = Session.new(title="t", persona="default", model="m")
    session.append(_event("user_message", "old-user", {"content": "马杜罗是什么情况"}))
    session.append(
        _event(
            "assistant_message",
            "partial-assistant",
            {"content": "我查一下马杜罗的最新情况。", "partial": True},
        )
    )
    session.append(
        _event(
            "user_message",
            "image-user",
            {
                "content": "",
                "attachments": [
                    {
                        "asset_id": "image-asset",
                        "display_name": "result.png",
                        "kind": "image",
                        "media_type": "image/png",
                        "size_bytes": 8,
                        "sha256": "image-asset",
                    }
                ],
            },
            run_id="run-2",
        )
    )

    projected = session.llm_messages

    assert [(message.role, message.content) for message in projected] == [
        ("user", "马杜罗是什么情况"),
        ("assistant", "上一轮回复已中断。"),
        ("user", ""),
    ]
    assert projected[1].meta["synthetic"] == "interrupted_assistant"
    assert projected[2].attachments[0].display_name == "result.png"
    assert session.messages[1].content == "我查一下马杜罗的最新情况。"
    assert session.messages[1].meta["partial"] is True
