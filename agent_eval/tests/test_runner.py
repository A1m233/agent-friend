from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.filesystem.tools import make_filesystem_tools
from agent.skills import SkillSelection
from agent.tools.builtin.skills import SKILL_CATALOG_SNAPSHOT_ARG, SkillReadTool
from agent_eval.runner import (
    _AssistantTextTrace,
    _build_fixture_skill_service,
    _CatalogGuardedLanguageModel,
    _FixtureTool,
    _import_fixture_attachment,
    _inject_current_session_events,
    _inject_current_session_messages,
    _inject_fixture_compaction,
    _inject_skill_catalog_state,
    _prior_tool_call_traces,
    _replace_fixture_skills,
    _session_runtime_config,
    _SkillCatalogRequestGuard,
)
from agent_eval.schema import (
    Case,
    CaseJudge,
    ExpectedSkillCatalogEntry,
    FakeTool,
    FakeToolResponse,
    FixtureAttachment,
    FixtureCompaction,
    FixtureCurrentEvent,
    FixtureMessage,
    FixtureSkill,
    FixtureToolCallRequest,
    FixtureToolCallResult,
)

from agent import AttachmentStore, JsonlSessionStore, Session
from llm_providers import LLMStreamEvent, ModelRequest, ModelResponse, ProviderSpec

FIXTURE_IMAGE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "unsupported-source-attribution"
    / "ambiguous-reaction.png"
)


def test_fixture_skill_update_runs_production_reload(tmp_path: Path) -> None:
    from agent.skills.lifecycle import apply_skill_update
    from agent.tool_runtime import ToolRunContext

    service = _build_fixture_skill_service(
        tmp_path,
        [FixtureSkill(name="report-helper", description="Report helper", instructions="old")],
    )
    target = next(r for r in service.snapshot.entries if r.name == "report-helper")
    turn = service.begin_turn((SkillSelection(target.id),))
    context = ToolRunContext.create("update")
    context.bind_skill(turn.activations[0])
    tool = _FixtureTool(
        FakeTool(
            name="exec_command",
            result_text="{}",
            skill_update={"name": "report-helper", "instructions": "new"},
        ),
        skill_service=service,
    )
    result = apply_skill_update(
        tool.invoke({}),
        service=service,
        snapshot=turn,
        context=context,
        runtime_capabilities="test",
    )
    assert not result.is_error
    assert context.skill_activations[target.id].contents.replace("\r\n", "\n").endswith("new\n")
    assert "old" in turn.activations[0].contents
    context.close_commands()


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


def test_fixture_command_lifecycle_tools_keep_production_specs() -> None:
    host_start = _FixtureTool(
        FakeTool(name="host_process_start", result_text='{"status":"exited"}')
    )
    write = _FixtureTool(FakeTool(name="write_stdin", result_text='{"status":"running"}'))
    terminate = _FixtureTool(
        FakeTool(name="terminate_command", result_text='{"status":"cancelled"}')
    )

    assert host_start.parameters_schema["required"] == ["argv"]
    assert host_start.parameters_schema["additionalProperties"] is False
    assert "结构化本地进程" in host_start.description
    assert write.parameters_schema["required"] == ["process_id"]
    assert "等待" in write.description
    assert terminate.parameters_schema["required"] == ["process_id"]
    assert terminate.parameters_schema["additionalProperties"] is False
    assert "可信终态" in terminate.description
    assert "改用替代命令或切换执行路径" in terminate.description
    assert "cancelled 本身就是可信终态" in terminate.description


def test_session_runtime_config_matches_provider_product_selection() -> None:
    config = _session_runtime_config(
        ProviderSpec(
            model="openai/deepseek-v4-flash-vision-exp",
            api_key="test-key",
            product_model_id="deepseek/deepseek-v4-flash-vision-exp",
            reasoning_preset="max",
        )
    )

    assert config.model_id == "deepseek/deepseek-v4-flash-vision-exp"
    assert config.reasoning_preset == "max"


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


def test_fixture_filesystem_tool_inherits_production_spec_from_registry_template() -> None:
    template = next(
        tool for tool in make_filesystem_tools() if tool.name == "request_filesystem_access"
    )
    fixture = _FixtureTool(
        FakeTool(name="request_filesystem_access", result_text='{"status":"denied"}'),
        template,
    )

    assert fixture.description == template.description
    assert fixture.parameters_schema == template.parameters_schema
    assert fixture.parameters_schema["required"] == ["reason", "items"]
    assert "当前用户轮" in fixture.description


def test_fixture_skills_use_production_catalog_and_reader(tmp_path: Path) -> None:
    service = _build_fixture_skill_service(
        tmp_path,
        [
            FixtureSkill(
                name="experiment-review",
                description="Use for baseline/candidate experiments.",
                instructions="Always compare baseline and candidate blindly.",
                allow_implicit_invocation=False,
            )
        ],
    )
    record = next(item for item in service.snapshot.entries if item.name == "experiment-review")
    assert record.allow_implicit_invocation is False

    turn = service.begin_turn((SkillSelection(record.id, expected_version=record.version),))
    assert len(turn.activations) == 1
    result = SkillReadTool(service).invoke(
        {
            "skill_id": record.id,
            "reason": "fixture direct-name test",
            SKILL_CATALOG_SNAPSHOT_ARG: turn.catalog,
        }
    )

    assert result.is_error is True
    assert result.meta["error_kind"] == "implicit_invocation_disabled"
    assert "Always compare baseline and candidate blindly." in turn.activations[0].contents


def test_fixture_skills_can_exercise_shared_production_discovery(tmp_path: Path) -> None:
    service = _build_fixture_skill_service(
        tmp_path,
        [
            FixtureSkill(
                name="shared-release-helper",
                description="Use for shared release checks.",
                instructions="Report the shared state.",
                source="shared",
            )
        ],
    )

    record = next(item for item in service.snapshot.entries if item.name == "shared-release-helper")
    assert record.source == "shared"
    assert record.entry_path == tmp_path / "shared" / "shared-release-helper" / "SKILL.md"


def test_package_fixture_skill_preserves_real_package_resources(tmp_path: Path) -> None:
    source = tmp_path / "source" / "package-helper"
    source.joinpath("references").mkdir(parents=True)
    source.joinpath("SKILL.md").write_text(
        "---\nname: package-helper\ndescription: Use for package fixtures.\n---\n\n"
        "Read references/guide.md.\n",
        encoding="utf-8",
    )
    source.joinpath("references", "guide.md").write_text("package evidence\n", encoding="utf-8")

    service = _build_fixture_skill_service(
        tmp_path / "discovery",
        [FixtureSkill(name="package-helper", package_path=source)],
    )

    record = next(item for item in service.snapshot.entries if item.name == "package-helper")
    assert record.allow_implicit_invocation is True
    assert (
        record.package_root.joinpath("references", "guide.md").read_text(encoding="utf-8")
        == "package evidence\n"
    )


def test_skill_catalog_request_guard_records_actual_unified_catalog(tmp_path: Path) -> None:
    case = Case(
        id="C21-test",
        query="q",
        facet="request guard",
        source="test",
        expected_skill_catalog_coverage="complete",
        expected_skill_catalog_entries=[
            ExpectedSkillCatalogEntry(name="agent-friend-guide", source="builtin"),
            ExpectedSkillCatalogEntry(name="personal-helper", source="personal"),
            ExpectedSkillCatalogEntry(name="shared-helper", source="shared"),
        ],
        judge=CaseJudge(must_recognize_catalog_skills=["agent-friend-guide"]),
    )
    service = _build_fixture_skill_service(
        tmp_path,
        [
            FixtureSkill(
                name="personal-helper",
                description="Personal fixture.",
                instructions="Read personal fixture.",
            ),
            FixtureSkill(
                name="shared-helper",
                description="Shared fixture.",
                instructions="Read shared fixture.",
                source="shared",
            ),
        ],
    )
    guard = _SkillCatalogRequestGuard(case)
    guard.prepare(service, model_window=100_000)
    content = service.begin_turn(model_window=100_000).rendered_catalog.content

    guard.observe(
        [
            {"role": "system", "content": "base prompt"},
            {"role": "system", "content": content},
            {"role": "user", "content": "q"},
        ]
    )

    observation = guard.observation
    assert observation is not None
    assert observation.preflight_passed is True
    assert observation.request_message_index == 1
    assert {(entry.name, entry.source) for entry in observation.request_entries} >= {
        ("agent-friend-guide", "builtin"),
        ("personal-helper", "personal"),
        ("shared-helper", "shared"),
    }


def test_skill_catalog_request_guard_blocks_before_delegate_call(tmp_path: Path) -> None:
    case = Case(
        id="C21-test",
        query="q",
        facet="request guard",
        source="test",
        expected_skill_catalog_coverage="complete",
        expected_skill_catalog_entries=[
            ExpectedSkillCatalogEntry(name="agent-friend-guide", source="builtin")
        ],
    )
    service = _build_fixture_skill_service(tmp_path, [])
    guard = _SkillCatalogRequestGuard(case)
    guard.prepare(service, model_window=100_000)

    class RecordingDelegate:
        called = False
        context_window = 100_000

        def generate(self, request: ModelRequest) -> ModelResponse:
            self.called = True
            return ModelResponse(text="")

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            self.called = True
            return iter(())

    delegate = RecordingDelegate()
    client = _CatalogGuardedLanguageModel(delegate, guard)

    with pytest.raises(ValueError, match="missing the production Skill catalog"):
        client.stream(ModelRequest(messages=[{"role": "user", "content": "q"}]))
    assert delegate.called is False


def test_prior_fixture_catalog_is_injected_as_trusted_system_state(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(
        title="catalog history",
        persona="default",
        model="fake/model",
        created_at=datetime.now(UTC),
    )
    store.create(session)
    service = _build_fixture_skill_service(
        tmp_path / "prior-catalog",
        [
            FixtureSkill(
                name="stable-writing-helper",
                description="Use for prose editing.",
                instructions="Preserve meaning.",
            )
        ],
    )

    _inject_skill_catalog_state(store, session, service, model_window=100_000)

    loaded = store.load(session.session_id)
    event = loaded.events[-1]
    assert event.type == "skill_catalog_state"
    assert "stable-writing-helper" in event.payload["content"]
    assert 'value="current"' in event.payload["content"]
    assert '<catalog_coverage entries="complete"' in event.payload["content"]
    assert loaded.llm_messages[-1].role == "system"


def test_prior_and_current_fixture_catalogs_share_stable_skill_identity(tmp_path: Path) -> None:
    prior = [
        FixtureSkill(
            name="stable-writing-helper",
            description="Use for prose editing.",
            instructions="Preserve meaning.",
        ),
        FixtureSkill(
            name="temporary-helper",
            description="Use for temporary checks.",
            instructions="Report the check.",
        ),
    ]
    current = [prior[0]]
    service = _build_fixture_skill_service(tmp_path, prior)
    prior_record = next(
        item for item in service.snapshot.entries if item.name == "stable-writing-helper"
    )

    _replace_fixture_skills(tmp_path, prior, current)
    service.refresh()

    current_record = next(
        item for item in service.snapshot.entries if item.name == "stable-writing-helper"
    )
    assert current_record.id == prior_record.id
    assert current_record.entry_path == prior_record.entry_path
    assert all(item.name != "temporary-helper" for item in service.snapshot.entries)


def test_truncated_catalog_fixture_omits_named_tail_skill(tmp_path: Path) -> None:
    fixtures = [
        FixtureSkill(
            name=f"aa-catalog-{index:02d}",
            description="Generic catalog fixture used to exercise bounded discovery rendering.",
            instructions="Answer only when selected.",
        )
        for index in range(1, 13)
    ]
    fixtures.append(
        FixtureSkill(
            name="zz-private-release-check",
            description="Use for checking private release package metadata.",
            instructions="Report release metadata.",
        )
    )
    service = _build_fixture_skill_service(tmp_path, fixtures)

    rendered = service.begin_turn(model_window=32_000).rendered_catalog

    assert rendered.omitted_count > 0
    assert '<catalog_coverage entries="truncated"' in rendered.content
    assert "zz-private-release-check" not in rendered.content


def test_fixture_skill_read_failure_keeps_production_tool_spec() -> None:
    tool = _FixtureTool(
        FakeTool(name="skill_read", result_text="Skill 读取失败，未激活。", is_error=True)
    )

    assert tool.parameters_schema["required"] == ["skill_id", "reason"]
    assert "成功结果才代表 Skill 已激活" in tool.description
    assert tool.invoke({"skill_id": "skill_1"}).is_error is True


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


def test_inject_fixture_compaction_uses_production_checkpoint_boundaries(
    tmp_path: Path,
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new(
        title="compaction replay",
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

    _inject_fixture_compaction(
        store,
        session,
        FixtureCompaction(summary="结构化摘要", covered_through_message=2),
    )

    loaded = store.load(session.session_id)
    checkpoint = loaded.latest_compaction()
    assert checkpoint is not None
    assert checkpoint.payload["summary"] == "结构化摘要"
    assert checkpoint.payload["checkpoint_version"] == 2
    assert checkpoint.payload["covered_through_uuid"] == loaded.messages[1].uuid
    assert checkpoint.payload["summary_source_from_uuid"] == loaded.messages[0].uuid
    assert checkpoint.payload["retained_from_uuid"] == ""


def test_inject_fixture_compaction_rejects_missing_message_boundary(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new(title="empty", persona="default", model="fake/model")
    store.create(session)

    with pytest.raises(ValueError, match="exceeds active message count"):
        _inject_fixture_compaction(
            store,
            session,
            FixtureCompaction(summary="摘要", covered_through_message=1),
        )


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


def test_prior_tool_call_trace_preserves_pre_execution_safety_facts() -> None:
    events: list[FixtureCurrentEvent] = [
        FixtureToolCallRequest(
            tool_call_id="exec-bad",
            tool_name="exec_command",
            args={},
            arguments_json="{}",
            execution_disposition="rejected_before_execution",
        ),
        FixtureToolCallResult(
            tool_call_id="exec-bad",
            tool_name="exec_command",
            content="rejected",
            is_error=True,
            executed=False,
            authorization_requested=False,
            error_code="invalid_tool_arguments",
        ),
    ]

    trace = _prior_tool_call_traces(events)

    assert len(trace) == 1
    assert trace[0].executed is False
    assert trace[0].authorization_requested is False
    assert trace[0].error_code == "invalid_tool_arguments"
