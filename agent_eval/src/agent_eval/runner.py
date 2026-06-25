"""跑单条 case 并收集 agent 行为 trace。"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Event,
    EventType,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    Session,
    SessionManager,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
    default_context_manager,
    make_default_registry,
)
from llm_providers import LLMClient, ProviderSpec

from .schema import Case, FixtureSession, ToolCallTrace, Trace


def _build_manager(store: JsonlSessionStore, spec: ProviderSpec) -> SessionManager:
    """装配一个挂指定 store 的 SessionManager。"""

    def _llm_factory(_model: str) -> LLMClient:
        return LLMClient(spec)

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    return SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=default_context_manager,
        tool_registry=make_default_registry(session_store=store, stderr_warn=False),
    )


def _inject_fixture(store: JsonlSessionStore, fixture: FixtureSession, model: str) -> None:
    """把 fixture session 写入 store，作为一条独立的过去 session。"""
    now = datetime.now(UTC)
    session_ts = now - timedelta(days=fixture.days_ago)
    session = Session.new(
        title=f"fixture-{fixture.days_ago}d-ago",
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


def run_case(case: Case, spec: ProviderSpec) -> Trace:
    """跑一个 case，返回完整 Trace；异常以 ``Trace.error`` 形式返回。"""
    try:
        with tempfile.TemporaryDirectory(prefix="agent-eval-") as tmp:
            store = JsonlSessionStore(Path(tmp))
            if case.fixture_session is not None:
                _inject_fixture(store, case.fixture_session, spec.model)

            manager = _build_manager(store, spec)
            session = manager.create(
                persona="default",
                model=spec.model,
                persona_id=BUILTIN_DEFAULT_PERSONA_ID,
            )
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
