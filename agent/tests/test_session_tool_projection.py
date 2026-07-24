from __future__ import annotations

from datetime import UTC, datetime

from agent import Event, Session


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
