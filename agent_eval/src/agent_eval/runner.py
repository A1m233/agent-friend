"""跑单条 case 并收集 agent 行为 trace。"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Event,
    EventType,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    Session,
    SessionCatalog,
    SessionManager,
    SessionMetadataStore,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    ToolRegistry,
    ToolResult,
    TurnDone,
    default_context_manager,
    make_default_registry,
)
from llm_providers import LLMClient, ProviderSpec

from .schema import Case, FakeTool, FixtureMessage, FixtureSession, ToolCallTrace, Trace


class _FixtureTool:
    """agent_eval case 专用固定结果工具。"""

    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def __init__(self, fixture: FakeTool) -> None:
        self.name = fixture.name
        self.description = (
            "搜索互联网获取实时或可能变化的信息。用户要求搜索或询问时效信息时使用。"
            if fixture.name == "web_search"
            else "读取完成当前请求所需的数据。"
        )
        self._fixture = fixture

    def invoke(self, _args: dict[str, Any]) -> ToolResult:
        return ToolResult(text=self._fixture.result_text, is_error=self._fixture.is_error)


def _build_manager(
    store: JsonlSessionStore,
    spec: ProviderSpec,
    *,
    tavily_api_key: str | None = None,
    fake_tool: FakeTool | None = None,
) -> SessionManager:
    """装配一个挂指定 store 的 SessionManager。"""

    def _llm_factory(_model: str) -> LLMClient:
        return LLMClient(spec)

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    metadata_store = SessionMetadataStore(store.base_dir / "metadata.json")
    session_catalog = SessionCatalog(store, metadata_store)
    registry = make_default_registry(
        tavily_api_key=tavily_api_key,
        session_store=store,
        session_catalog=session_catalog,
        stderr_warn=False,
    )
    if fake_tool is not None:
        tools = [tool for tool in registry.all_tools() if tool.name != fake_tool.name]
        tools.append(cast(Any, _FixtureTool(fake_tool)))
        registry = ToolRegistry(tools)

    return SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=default_context_manager,
        tool_registry=registry,
        metadata_store=metadata_store,
    )


def _inject_fixture(store: JsonlSessionStore, fixture: FixtureSession, model: str) -> None:
    """把 fixture session 写入 store，作为一条独立的过去 session。"""
    now = datetime.now(UTC)
    session_ts = now - timedelta(days=fixture.days_ago)
    session = Session.new(
        title=fixture.title or f"fixture-{fixture.days_ago}d-ago",
        persona="default",
        model=model,
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        session_id=str(uuid4()),
        created_at=session_ts,
    )
    for index, msg in enumerate(fixture.messages):
        event_type: EventType = "user_message" if msg.role == "user" else "assistant_message"
        session.append(
            Event(
                type=event_type,
                uuid=str(uuid4()),
                ts=session_ts + timedelta(minutes=index + 1),
                payload={"content": msg.text},
                meta={},
            )
        )
    store.create(session)


def _inject_current_session_messages(
    store: JsonlSessionStore,
    session: Session,
    messages: list[FixtureMessage],
) -> None:
    """把前置轮次注入当前被测会话，不触发 LLM。"""
    now = datetime.now(UTC)
    for index, msg in enumerate(messages):
        event_type: EventType = "user_message" if msg.role == "user" else "assistant_message"
        event = Event(
            type=event_type,
            uuid=str(uuid4()),
            ts=now + timedelta(milliseconds=index),
            payload={"content": msg.text},
            meta={},
        )
        session.append(event)
        store.append_event(session.session_id, event)


def run_case(case: Case, spec: ProviderSpec, *, tavily_api_key: str | None = None) -> Trace:
    """跑一个 case，返回完整 Trace；异常以 ``Trace.error`` 形式返回。"""
    try:
        with tempfile.TemporaryDirectory(prefix="agent-eval-") as tmp:
            store = JsonlSessionStore(Path(tmp))
            if case.fixture_session is not None:
                _inject_fixture(store, case.fixture_session, spec.model)

            manager = _build_manager(
                store,
                spec,
                tavily_api_key=tavily_api_key,
                fake_tool=case.fake_tool,
            )
            session = manager.create(
                persona="default",
                model=spec.model,
                persona_id=BUILTIN_DEFAULT_PERSONA_ID,
            )
            _inject_current_session_messages(store, session, case.current_session_messages)
            conversation = manager.start_conversation(session)

            tool_requests: dict[str, ToolCallRequest] = {}
            tool_calls: list[ToolCallTrace] = []
            text_buf: list[str] = []
            stop_reason = "unknown"

            for event in conversation.stream(case.query):
                if isinstance(event, ToolCallRequest):
                    tool_requests[event.tool_call_id] = event
                elif isinstance(event, ToolCallResult):
                    request = tool_requests.pop(event.tool_call_id, None)
                    tool_calls.append(
                        ToolCallTrace(
                            name=event.tool_name,
                            args=dict(request.args) if request else {},
                            result_text=event.text,
                            is_error=event.is_error,
                            duration_seconds=event.duration_seconds,
                        )
                    )
                elif isinstance(event, TextDelta):
                    text_buf.append(event.text)
                elif isinstance(event, TurnDone):
                    stop_reason = event.stop_reason

            for request in tool_requests.values():
                tool_calls.append(
                    ToolCallTrace(
                        name=request.tool_name,
                        args=dict(request.args),
                        result_text="",
                        is_error=False,
                        duration_seconds=None,
                    )
                )

            return Trace(
                case_id=case.id,
                tool_calls=tool_calls,
                final_text="".join(text_buf),
                stop_reason=stop_reason,
            )
    except Exception as exc:
        return Trace(
            case_id=case.id,
            tool_calls=[],
            final_text="",
            stop_reason="error",
            error=f"{type(exc).__name__}: {exc}",
        )
