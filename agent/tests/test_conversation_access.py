from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent.sessions.access.service import ConversationAccessError, ConversationAccessService
from agent.sessions.events import EventType
from agent.tools.builtin.conversation_access import ReadConversationTool

from agent import Event, JsonlSessionStore, Session


def event(
    kind: EventType, uid: str, *, meta: dict[str, Any] | None = None, **payload: Any
) -> Event:
    return Event(type=kind, uuid=uid, ts=datetime.now(UTC), payload=payload, meta=meta or {})


@pytest.fixture
def access(tmp_path: Path) -> tuple[JsonlSessionStore, ConversationAccessService]:
    store = JsonlSessionStore(tmp_path)
    store.create(Session.new("Daily", "default", "test/model", session_id="daily"))
    for i in range(6):
        store.append_events(
            "daily",
            [
                event("user_message", f"u{i}", content=f"q{i}"),
                event("assistant_message", f"a{i}", content=f"r{i}"),
            ],
        )
    return store, ConversationAccessService(store)


def test_exact_message_count_and_stable_older_page(
    access: tuple[JsonlSessionStore, ConversationAccessService], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, service = access
    first = service.read_conversation(session_id="daily", limit=3)
    assert [m["message_id"] for m in first["messages"]] == ["a4", "u5", "a5"]
    store.append_event("daily", event("user_message", "new", content="after snapshot"))

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("indexed message paging must not reload all bodies")

    monkeypatch.setattr(store, "_read_records", forbidden)
    older = service.read_conversation(
        session_id="daily", mode="continue", cursor=first["older_cursor"], limit=3
    )
    assert [m["message_id"] for m in older["messages"]] == ["u3", "a3", "u4"]


def test_increment_is_frozen_and_pages_forward(
    access: tuple[JsonlSessionStore, ConversationAccessService],
) -> None:
    store, service = access
    boundary = store.query_messages("daily", limit=1)
    store.append_events(
        "daily",
        [
            event("user_message", "extra-u", content="hello"),
            event("assistant_message", "extra-a", content="world"),
        ],
    )
    upper = store.query_messages("daily", limit=1)
    service.wake_snapshot = lambda _: {
        "sessions": {
            "daily": {"lower": boundary.upper - 1, "upper": upper.upper, "epoch": upper.epoch}
        }
    }
    store.append_event("daily", event("user_message", "too-new", content="next wake"))
    first = service.read_conversation(session_id="daily", mode="since_wake", limit=1)
    assert [m["message_id"] for m in first["messages"]] == ["extra-u"]
    second = service.read_conversation(
        session_id="daily", mode="continue", cursor=first["next_cursor"], limit=10
    )
    assert [m["message_id"] for m in second["messages"]] == ["extra-a"]
    assert second["next_cursor"] is None


def test_hidden_and_partial_events_do_not_count_as_messages(
    access: tuple[JsonlSessionStore, ConversationAccessService],
) -> None:
    store, _ = access
    store.append_events(
        "daily",
        [
            event("system_trigger", "trigger", presentation="none", meta={"run_id": "hidden"}),
            event(
                "assistant_message", "hidden-answer", content="secret", meta={"run_id": "hidden"}
            ),
            event("assistant_message", "partial", content="in flight", partial=True),
            event("tool_call_result", "tool", result="tool result"),
            event(
                "user_message",
                "scheduled",
                content="synthetic",
                request_source={"kind": "scheduled_task"},
            ),
        ],
    )
    page = store.query_messages("daily", limit=100)
    assert len(page.messages) == 12
    assert page.counts == {"user": 6, "assistant": 6, "proactive": 0}


def test_long_message_has_complete_content_continuation(
    access: tuple[JsonlSessionStore, ConversationAccessService],
) -> None:
    store, service = access
    content = "abcdef" * 1500
    store.append_event("daily", event("user_message", "long", content=content))
    first = service.read_conversation(session_id="daily", limit=1)["messages"][0]
    pieces = [first["text"]]
    cursor = first["content_cursor"]
    while cursor:
        part = service.read_conversation(session_id="daily", mode="continue", cursor=cursor)[
            "messages"
        ][0]
        pieces.append(part["text"])
        cursor = part["content_cursor"]
    assert "".join(pieces) == content


def test_rewrite_invalidates_cursor_and_deleted_content_is_not_returned(
    access: tuple[JsonlSessionStore, ConversationAccessService],
) -> None:
    store, service = access
    first = service.read_conversation(session_id="daily", limit=2)
    store.append_event("daily", event("turn_rewrite", "edit", inactive_event_uuids=["u3", "a3"]))
    with pytest.raises(ConversationAccessError, match="历史"):
        service.read_conversation(session_id="daily", mode="continue", cursor=first["older_cursor"])
    current = service.read_conversation(session_id="daily", limit=100)
    assert "u3" not in {m["message_id"] for m in current["messages"]}


def test_inner_world_not_discoverable_or_readable(
    access: tuple[JsonlSessionStore, ConversationAccessService],
) -> None:
    store, service = access
    store.create(
        Session.new("Inner", "default", "test/model", session_id="inner", origin_kind="inner_world")
    )
    assert [e["session_id"] for e in service.list_conversations()["conversations"]] == ["daily"]
    result = ReadConversationTool(service).invoke({"session_id": "inner", "mode": "latest"})
    assert result.is_error
    assert json.loads(result.text)["error"] == "target_missing"


def test_wrong_cursor_and_missing_wake_return_actionable_errors(
    access: tuple[JsonlSessionStore, ConversationAccessService],
) -> None:
    _, service = access
    tool = ReadConversationTool(service)
    result = tool.invoke({"session_id": "daily", "mode": "since_wake"})
    assert result.is_error and "wake_context_unavailable" in result.text
    result = tool.invoke({"session_id": "daily", "mode": "continue", "cursor": "daily"})
    assert result.is_error and "invalid_cursor" in result.text
