from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_eval.runner import (
    _AssistantTextTrace,
    _FixtureTool,
    _import_fixture_attachment,
    _inject_current_session_messages,
)
from agent_eval.schema import FakeTool, FixtureAttachment, FixtureMessage

from agent import AttachmentStore, JsonlSessionStore, Session

FIXTURE_IMAGE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "unsupported-source-attribution"
    / "ambiguous-reaction.png"
)


def test_fixture_tool_returns_case_local_result_without_external_call() -> None:
    tool = _FixtureTool(
        FakeTool(name="web_search", result_text='{"trust":"untrusted_data"}', is_error=False)
    )

    result = tool.invoke({"search_text": "ignored"})

    assert tool.name == "web_search"
    assert "时效性信息" in tool.description
    assert result.text == '{"trust":"untrusted_data"}'
    assert result.is_error is False


def test_fixture_web_tools_keep_production_specs_and_search_source_urls() -> None:
    search = _FixtureTool(
        FakeTool(
            name="web_search",
            result_text="search result",
            source_urls=["https://example.com/docs"],
        )
    )
    fetch = _FixtureTool(FakeTool(name="web_fetch", result_text="page body"))

    assert search.parameters_schema["required"] == ["query"]
    assert search.invoke({"query": "q"}).meta["source_urls"] == ["https://example.com/docs"]
    assert fetch.parameters_schema["oneOf"] == [
        {"required": ["url"]},
        {"required": ["evidence_ref"]},
    ]
    assert "官方原文" in fetch.description


def test_assistant_text_trace_preserves_tool_continuation_segments() -> None:
    trace = _AssistantTextTrace()

    trace.add("我先看看")
    trace.boundary()
    trace.add("找到官方博客了，")
    trace.add("不过摘要没写默认值，我再打开原文确认。")
    trace.boundary()
    trace.add("原文没有声明默认 TTL，示例值是 3600 秒。")
    trace.boundary()

    assert trace.segments == [
        "我先看看",
        "找到官方博客了，不过摘要没写默认值，我再打开原文确认。",
        "原文没有声明默认 TTL，示例值是 3600 秒。",
    ]
    assert trace.final_text == "".join(trace.segments)


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


def test_inject_current_session_message_persists_multimodal_fixture(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    attachment_store = AttachmentStore(tmp_path / "attachments")
    fixture = FixtureAttachment(path=FIXTURE_IMAGE, display_name="ambiguous-reaction.png")
    ref = _import_fixture_attachment(attachment_store, fixture)
    session = Session.new(
        title="multimodal replay",
        persona="default",
        model="kimi/kimi-k2.6",
        created_at=datetime.now(UTC),
    )
    store.create(session)

    _inject_current_session_messages(
        store,
        session,
        [FixtureMessage(role="user", text="", attachments=[fixture])],
        resolve_attachments=lambda _items: [ref],
    )

    event = store.load(session.session_id).events[-1]
    attachment = event.payload["attachments"][0]
    assert event.payload["content"] == ""
    assert attachment["asset_id"] == ref.asset_id
    assert attachment["media_type"] == "image/png"
    with attachment_store.open(ref.asset_id) as source:
        assert source.read(8) == b"\x89PNG\r\n\x1a\n"
