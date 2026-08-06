from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_eval.runner import (
    _AssistantTextTrace,
    _FixtureTool,
    _import_fixture_attachment,
    _inject_current_session_events,
    _inject_current_session_messages,
)
from agent_eval.schema import (
    FakeTool,
    FakeToolResponse,
    FixtureAttachment,
    FixtureMessage,
    FixtureToolCallRequest,
    FixtureToolCallResult,
)

from agent import AttachmentStore, JsonlSessionStore, Session

FIXTURE_IMAGE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "unsupported-source-attribution"
    / "ambiguous-reaction.png"
)


def test_fixture_tool_returns_case_local_result_without_external_call() -> None:
    tool = _FixtureTool(
        FakeTool(name="web_search", result_text='{"source_type":"web_search"}', is_error=False)
    )

    result = tool.invoke({"search_text": "ignored"})

    assert tool.name == "web_search"
    assert "时效性信息" in tool.description
    assert result.text == '{"source_type":"web_search"}'
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


def test_fixture_tool_selects_response_by_argument_subset() -> None:
    tool = _FixtureTool(
        FakeTool(
            name="web_fetch",
            responses=[
                FakeToolResponse(
                    match_args={"evidence_ref": "first_ref"},
                    result_text="first page",
                ),
                FakeToolResponse(
                    match_args={"evidence_ref": "target_ref"},
                    result_text="target page",
                ),
            ],
        )
    )

    result = tool.invoke(
        {
            "evidence_ref": "target_ref",
            "focus": "核对正文",
            "__agent_friend_url_authorization": {"source": "fixture"},
        }
    )

    assert result.text == "target page"
    assert result.is_error is False


def test_fixture_tool_rejects_unmatched_or_ambiguous_arguments() -> None:
    unmatched = _FixtureTool(
        FakeTool(
            name="web_fetch",
            responses=[
                FakeToolResponse(
                    match_args={"evidence_ref": "target_ref"},
                    result_text="target page",
                )
            ],
        )
    ).invoke({"evidence_ref": "other_ref"})
    assert unmatched.is_error is True
    assert unmatched.meta["error_kind"] == "fixture_no_match"

    ambiguous = _FixtureTool(
        FakeTool(
            name="web_fetch",
            responses=[
                FakeToolResponse(
                    match_args={"evidence_ref": "target_ref"},
                    result_text="broad",
                ),
                FakeToolResponse(
                    match_args={"evidence_ref": "target_ref", "focus": "名单"},
                    result_text="specific",
                ),
            ],
        )
    ).invoke({"evidence_ref": "target_ref", "focus": "名单"})
    assert ambiguous.is_error is True
    assert ambiguous.meta["error_kind"] == "fixture_ambiguous_match"


def test_fixture_social_tools_keep_production_specs() -> None:
    search = _FixtureTool(FakeTool(name="social_search", result_text="search result"))
    read = _FixtureTool(FakeTool(name="social_read", result_text="detail"))

    assert search.parameters_schema["required"] == ["platform", "query"]
    assert read.parameters_schema["oneOf"] == [
        {"required": ["resource_ref"]},
        {"required": ["url"]},
        {"required": ["evidence_ref"]},
        {"required": ["continuation_ref"]},
    ]


def test_fixture_weather_and_todo_tools_keep_production_specs() -> None:
    weather = _FixtureTool(
        FakeTool(name="weather_query", result_text='{"status":"temporary_failure"}')
    )
    todo = _FixtureTool(FakeTool(name="todo_manage", result_text='{"status":"success"}'))

    assert weather.parameters_schema["required"] == ["location"]
    assert "不会后台自动重试" in weather.description
    assert todo.parameters_schema["required"] == ["operation"]
    reminder = todo.parameters_schema["properties"]["reminders"]["items"]
    assert reminder["required"] == ["kind", "timezone"]
    assert reminder["properties"]["kind"]["enum"] == ["once", "recurring"]
    assert reminder["properties"]["relative_delay"]["required"] == ["amount", "unit"]
    assert "不能只在对话里口头答应" in todo.description
    assert "本轮不得创建或修改 Reminder" in todo.description
    assert (
        "reminders 就是本次调用的必填参数"
        in todo.parameters_schema["properties"]["operation"]["description"]
    )
    assert "不得省略" in todo.parameters_schema["properties"]["reminders"]["description"]


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


def test_inject_current_session_events_preserves_completed_tool_history(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new(
        title="tool replay",
        persona="default",
        model="fake/model",
        created_at=datetime.now(UTC),
    )
    store.create(session)

    _inject_current_session_events(
        store,
        session,
        [
            FixtureMessage(role="assistant", text="我搜一下。"),
            FixtureToolCallRequest(
                tool_call_id="web_search_58",
                tool_name="web_search",
                args={"query": "时代少年团 魔丸"},
            ),
            FixtureToolCallResult(
                tool_call_id="web_search_58",
                tool_name="web_search",
                content='{"status":"no_results"}',
            ),
            FixtureMessage(role="assistant", text="没搜到这个具体梗。"),
        ],
    )

    loaded = store.load(session.session_id)
    assert [event.type for event in loaded.events[-4:]] == [
        "assistant_message",
        "tool_call_request",
        "tool_call_result",
        "assistant_message",
    ]
    messages = loaded.llm_messages[-3:]
    assert [message.role for message in messages] == ["assistant", "tool", "assistant"]
    assert messages[0].meta["tool_calls"] == [
        {
            "id": "web_search_58",
            "name": "web_search",
            "args": {"query": "时代少年团 魔丸"},
        }
    ]
    assert messages[1].content == '{"status":"no_results"}'
    assert messages[1].meta["tool_call_id"] == "web_search_58"
