from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_eval.runner import _FixtureTool, _inject_current_session_messages
from agent_eval.schema import FakeTool, FixtureMessage

from agent import JsonlSessionStore, Session


def test_fixture_tool_returns_case_local_result_without_external_call() -> None:
    tool = _FixtureTool(
        FakeTool(name="web_search", result_text='{"trust":"untrusted_data"}', is_error=False)
    )

    result = tool.invoke({"query": "ignored"})

    assert tool.name == "web_search"
    assert "时效信息" in tool.description
    assert result.text == '{"trust":"untrusted_data"}'
    assert result.is_error is False


def test_inject_current_session_messages_populates_active_session_without_llm(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new(
        title="replay",
        persona="default",
        model="fake/model",
        created_at=datetime.now(UTC),
    )
    store.create(session)

    _inject_current_session_messages(
        store,
        session,
        [
            FixtureMessage(role="user", text="前置问题"),
            FixtureMessage(role="assistant", text="前置回答"),
        ],
    )

    assert [event.type for event in session.events[-2:]] == [
        "user_message",
        "assistant_message",
    ]
    assert [event.payload["content"] for event in store.load(session.session_id).events[-2:]] == [
        "前置问题",
        "前置回答",
    ]
