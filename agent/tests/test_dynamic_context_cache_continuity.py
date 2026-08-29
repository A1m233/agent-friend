from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from agent.sessions import SessionCacheEpoch, SessionModelContext
from agent.system_prompt import DESKTOP_CHAT_SURFACE, IM_QQ_SURFACE, InteractionSurfaceId
from agent.system_prompt.sections import PromptChannel
from agent.system_prompt.surfaces import DEFAULT_INTERACTION_CONTEXT_RENDERER
from agent.tools.builtin.datetime_calculate import DateTimeCalculateTool

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    ToolRegistry,
    ToolResult,
)
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ProviderAssistantTurn,
    ProviderToolCall,
)


@dataclass
class _MutableTimeProvider:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass
class _SteppingTimeProvider:
    values: list[datetime]
    index: int = 0

    def now(self) -> datetime:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


@dataclass
class _RecordingLLM:
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    replies: list[str] = field(default_factory=lambda: ["ok"])
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.received.append(copy.deepcopy(request.messages))
        return ModelResponse(text=self.replies[0])

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.received.append(copy.deepcopy(request.messages))
        reply = self.replies[min(len(self.received) - 1, len(self.replies) - 1)]
        yield LLMTextDelta(text=reply)
        yield LLMTurnDone(
            stop_reason="end_turn",
            assistant_turn=ProviderAssistantTurn(content=reply),
        )


class _EchoTool:
    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "Return the supplied message."
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(text=str(args["message"]))


@dataclass
class _ToolLoopLLM:
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("stream expected")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.received.append(copy.deepcopy(request.messages))
        if len(self.received) == 1:
            yield LLMToolCallDelta(
                index=0,
                tool_call_id="call-1",
                tool_name="echo",
                args_json_delta='{"message":"hello"}',
            )
            yield LLMTurnDone(
                stop_reason="tool_use",
                assistant_turn=ProviderAssistantTurn(
                    content=None,
                    tool_calls=(
                        ProviderToolCall(
                            index=0,
                            tool_call_id="call-1",
                            tool_name="echo",
                            arguments_json='{"message":"hello"}',
                        ),
                    ),
                ),
            )
            return
        yield LLMTextDelta(text="done")
        yield LLMTurnDone(
            stop_reason="end_turn",
            assistant_turn=ProviderAssistantTurn(content="done"),
        )


@dataclass
class _CurrentTimeLLM:
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("stream expected")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.received.append(copy.deepcopy(request.messages))
        if len(self.received) == 1:
            yield LLMToolCallDelta(
                index=0,
                tool_call_id="time-1",
                tool_name="datetime_calculate",
                args_json_delta='{"operation":"current"}',
            )
            yield LLMTurnDone(stop_reason="tool_use")
            return
        yield LLMTextDelta(text="done")
        yield LLMTurnDone(stop_reason="end_turn")


def _manager(
    tmp_path: Path,
    llm: _RecordingLLM | _ToolLoopLLM | _CurrentTimeLLM,
    clock: _MutableTimeProvider | _SteppingTimeProvider,
    *,
    tool_registry: ToolRegistry | None = None,
) -> tuple[SessionManager, str]:
    store = JsonlSessionStore(tmp_path / "sessions")
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    manager = SessionManager(
        store=store,
        llm_client_factory=lambda _config: llm,
        prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
            persona_id=persona_id,
            catalog=catalog,
        ),
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
        time_provider=clock,
    )
    session = manager.create(
        persona="default",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        model="deepseek/deepseek-chat",
        title="cache continuity",
    )
    return manager, session.session_id


def _run(
    manager: SessionManager,
    session_id: str,
    text: str,
    *,
    surface: InteractionSurfaceId = DESKTOP_CHAT_SURFACE,
    channel: PromptChannel | None = None,
    turn_context: str | None = None,
) -> None:
    conversation = manager.start_conversation(manager.open(session_id))
    list(
        conversation.stream(
            text,
            surface=surface,
            channel=channel,
            turn_context=turn_context,
        )
    )


def _count(messages: list[dict[str, Any]], marker: str) -> int:
    return sum(marker in str(message.get("content") or "") for message in messages)


def test_ordinary_user_turn_strictly_extends_previous_wire(tmp_path: Path) -> None:
    clock = _MutableTimeProvider(datetime(2026, 8, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    llm = _RecordingLLM(replies=["first answer", "second answer"])
    manager, session_id = _manager(tmp_path, llm, clock)

    _run(manager, session_id, "first")
    _run(manager, session_id, "second")

    first, second = llm.received
    assert second[: len(first)] == first
    assert second[len(first)]["role"] == "assistant"
    assert second[len(first)]["content"] == "first answer"
    assert second[len(first) + 1]["role"] == "user"
    assert second[len(first) + 1]["content"] == "second"
    assert _count(second, 'contributor="calendar"') == 1


def test_tool_continuation_only_appends_assistant_and_tool_result(tmp_path: Path) -> None:
    clock = _MutableTimeProvider(datetime(2026, 8, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    llm = _ToolLoopLLM()
    manager, session_id = _manager(
        tmp_path,
        llm,
        clock,
        tool_registry=ToolRegistry([_EchoTool()]),
    )

    _run(manager, session_id, "use the tool", turn_context="trusted current-turn boundary")

    first, continuation = llm.received
    assert continuation[: len(first)] == first
    assert [item["role"] for item in continuation[len(first) :]] == ["assistant", "tool"]
    assert _count(continuation, 'contributor="calendar"') == 1
    assert _count(continuation, "trusted current-turn boundary") == 1


def test_calendar_ignores_minutes_and_appends_day_and_timezone_deltas(tmp_path: Path) -> None:
    clock = _MutableTimeProvider(datetime(2026, 8, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    llm = _RecordingLLM(replies=["a", "b", "c", "d"])
    manager, session_id = _manager(tmp_path, llm, clock)

    _run(manager, session_id, "one")
    clock.value = datetime(2026, 8, 29, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    _run(manager, session_id, "two")
    clock.value = datetime(2026, 8, 30, 0, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    _run(manager, session_id, "three")
    clock.value = datetime(2026, 8, 29, 9, 2, tzinfo=ZoneInfo("America/Los_Angeles"))
    _run(manager, session_id, "four")

    first, minute, next_day, new_zone = llm.received
    assert minute[: len(first)] == first
    assert _count(minute, 'contributor="calendar"') == 1
    assert next_day[: len(minute)] == minute
    assert _count(next_day, 'contributor="calendar"') == 2
    assert 'date="2026-08-30" timezone="Asia/Shanghai"' in str(next_day)
    assert new_zone[: len(next_day)] == next_day
    assert _count(new_zone, 'contributor="calendar"') == 3
    assert 'date="2026-08-29" timezone="America/Los_Angeles"' in str(new_zone)


def test_current_tool_reads_fresh_action_time_not_turn_snapshot(tmp_path: Path) -> None:
    old = datetime(2026, 8, 29, 12, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    fresh = datetime(2026, 8, 29, 12, 34, 56, tzinfo=ZoneInfo("Asia/Shanghai"))
    clock = _SteppingTimeProvider([old, old, fresh, fresh])
    llm = _CurrentTimeLLM()
    manager, session_id = _manager(
        tmp_path,
        llm,
        clock,
        tool_registry=ToolRegistry([DateTimeCalculateTool()]),
    )

    _run(manager, session_id, "现在几点？")

    tool_result = next(message for message in llm.received[1] if message.get("role") == "tool")
    assert "2026-08-29T12:34:56+08:00" in tool_result["content"]
    assert "2026-08-29T12:30:00+08:00" not in tool_result["content"]


def test_surface_change_appends_only_surface_contributor_delta(tmp_path: Path) -> None:
    clock = _MutableTimeProvider(datetime(2026, 8, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    llm = _RecordingLLM(replies=["a", "b"])
    manager, session_id = _manager(tmp_path, llm, clock)

    _run(manager, session_id, "desktop", surface=DESKTOP_CHAT_SURFACE)
    _run(manager, session_id, "im", surface=IM_QQ_SURFACE)

    first, second = llm.received
    assert second[: len(first)] == first
    appended = second[len(first) :]
    assert [item["role"] for item in appended] == ["assistant", "user", "user"]
    assert 'contributor="surface" kind="delta"' in appended[1]["content"]
    assert appended[2]["content"] == "im"
    assert _count(second, 'contributor="calendar"') == 1
    assert _count(second, "<agent_friend_runtime_capabilities") == 1


def test_channel_change_is_a_delta_not_a_system_head_rewrite(tmp_path: Path) -> None:
    clock = _MutableTimeProvider(datetime(2026, 8, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    llm = _RecordingLLM(replies=["a", "b"])
    manager, session_id = _manager(tmp_path, llm, clock)

    _run(manager, session_id, "text", channel="text")
    _run(manager, session_id, "voice", channel="voice")

    first, second = llm.received
    assert second[: len(first)] == first
    assert second[0] == first[0]
    appended = second[len(first) :]
    assert [item["role"] for item in appended] == ["assistant", "system", "user"]
    assert 'channel="voice"' in appended[1]["content"]
    assert "正在通过电话和用户对话" in appended[1]["content"]


def test_fixed_model_context_state_emits_only_the_changed_contributor() -> None:
    now = datetime(2026, 8, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    state = SessionModelContext()

    baseline = state.plan(
        current_time=now,
        timezone="Asia/Shanghai",
        surface="desktop_chat",
        surface_context=DEFAULT_INTERACTION_CONTEXT_RENDERER.render_snapshot(
            surface=DESKTOP_CHAT_SURFACE
        ),
        channel="text",
        channel_instruction=None,
        approval_mode="per_request",
        approval_version=0,
        runtime_capabilities="capabilities-a",
        capability_catalog=None,
    )
    state.commit(baseline)
    permission = state.plan(
        current_time=now,
        timezone="Asia/Shanghai",
        surface="desktop_chat",
        surface_context=DEFAULT_INTERACTION_CONTEXT_RENDERER.render_snapshot(
            surface=DESKTOP_CHAT_SURFACE
        ),
        channel="text",
        channel_instruction=None,
        approval_mode="session_auto",
        approval_version=1,
        runtime_capabilities="capabilities-a",
        capability_catalog=None,
    )

    assert len(permission.messages) == 1
    assert permission.messages[0].meta["model_context_contributor"] == "permission"
    state.commit(permission)
    capabilities = state.plan(
        current_time=now,
        timezone="Asia/Shanghai",
        surface="desktop_chat",
        surface_context=DEFAULT_INTERACTION_CONTEXT_RENDERER.render_snapshot(
            surface=DESKTOP_CHAT_SURFACE
        ),
        channel="text",
        channel_instruction=None,
        approval_mode="session_auto",
        approval_version=1,
        runtime_capabilities="capabilities-b",
        capability_catalog=None,
    )
    assert len(capabilities.messages) == 1
    assert capabilities.messages[0].meta["model_context_contributor"] == "runtime_capabilities"
    state.commit(capabilities)
    catalog = state.plan(
        current_time=now,
        timezone="Asia/Shanghai",
        surface="desktop_chat",
        surface_context=DEFAULT_INTERACTION_CONTEXT_RENDERER.render_snapshot(
            surface=DESKTOP_CHAT_SURFACE
        ),
        channel="text",
        channel_instruction=None,
        approval_mode="session_auto",
        approval_version=1,
        runtime_capabilities="capabilities-b",
        capability_catalog="catalog-a",
    )
    assert len(catalog.messages) == 1
    assert catalog.messages[0].meta["model_context_contributor"] == "capability_catalog"
    state.commit(catalog)
    catalog_removed = state.plan(
        current_time=now,
        timezone="Asia/Shanghai",
        surface="desktop_chat",
        surface_context=DEFAULT_INTERACTION_CONTEXT_RENDERER.render_snapshot(
            surface=DESKTOP_CHAT_SURFACE
        ),
        channel="text",
        channel_instruction=None,
        approval_mode="session_auto",
        approval_version=1,
        runtime_capabilities="capabilities-b",
        capability_catalog=None,
    )
    assert len(catalog_removed.messages) == 1
    assert catalog_removed.messages[0].meta["model_context_contributor"] == "capability_catalog"
    assert 'available="false"' in catalog_removed.messages[0].content


def test_cache_epoch_classifies_every_envelope_and_history_rewrite_boundary() -> None:
    baseline: dict[str, Any] = {
        "persona_id": "persona-a",
        "system_prompt": "system-a",
        "tools": [{"type": "function", "function": {"name": "tool-a"}}],
        "settings": {"temperature": 0},
        "model_route": "provider-a/model-a",
    }
    cases: list[tuple[dict[str, Any], str]] = [
        ({"persona_id": "persona-b"}, "persona_changed"),
        ({"system_prompt": "system-b"}, "system_prompt_changed"),
        (
            {"tools": [{"type": "function", "function": {"name": "tool-b"}}]},
            "core_tools_changed",
        ),
        ({"settings": {"temperature": 1}}, "request_settings_changed"),
        ({"model_route": "provider-b/model-a"}, "model_route_changed"),
    ]

    for override, expected_reason in cases:
        epoch = SessionCacheEpoch()
        first = epoch.observe(**baseline)
        assert first.changed is False
        changed = epoch.observe(**{**baseline, **override})
        assert changed.changed is True
        assert changed.generation == 1
        assert changed.reason == expected_reason

    epoch = SessionCacheEpoch()
    epoch.observe(**baseline)
    compacted = epoch.observe(**baseline, history_rewrite_reason="compaction")
    assert compacted.changed is True
    assert compacted.reason == "compaction"


def test_restart_is_degraded_and_does_not_persist_model_context(tmp_path: Path) -> None:
    clock = _MutableTimeProvider(datetime(2026, 8, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    first_llm = _RecordingLLM(replies=["answer"])
    manager, session_id = _manager(tmp_path, first_llm, clock)
    _run(manager, session_id, "first")

    persisted = (tmp_path / "sessions" / f"{session_id}.jsonl").read_text(encoding="utf-8")
    assert "agent_friend_environment_context" not in persisted
    assert "agent_friend_runtime_capabilities" not in persisted

    restarted_llm = _RecordingLLM(replies=["after restart"])
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    restarted = SessionManager(
        store=JsonlSessionStore(tmp_path / "sessions"),
        llm_client_factory=lambda _config: restarted_llm,
        prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
            persona_id=persona_id,
            catalog=catalog,
        ),
        context_manager_factory=NaiveContextManager,
        time_provider=clock,
    )
    conversation = restarted.start_conversation(restarted.open(session_id))

    assert conversation.cache_epoch_diagnostics == {
        "generation": 0,
        "reason": "process_resume_degraded",
        "exact_process_continuation": False,
    }
    list(conversation.stream("continue", surface=DESKTOP_CHAT_SURFACE))
    assert restarted_llm.received[0][: len(first_llm.received[0])] != first_llm.received[0]
