"""跑单条 case 并收集 agent 行为 trace。"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from agent.system_prompt import InteractionSurfaceId
from agent.tool_runtime import ToolRunContext
from agent.tools.builtin.web_fetch import WebFetchTool
from agent.tools.builtin.web_search import WebSearchTool

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    AttachmentRef,
    AttachmentStore,
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
from llm_providers import LLMClient, MultimodalInputAdapter, ProviderSpec

from .schema import (
    Case,
    FakeTool,
    FixtureAttachment,
    FixtureCurrentEvent,
    FixtureMessage,
    FixtureSession,
    FixtureToolCallRequest,
    FixtureToolCallResult,
    ToolCallTrace,
    Trace,
)

ResolveAttachments = Callable[[list[FixtureAttachment]], list[AttachmentRef]]


@dataclass
class _AssistantTextTrace:
    """Collect visible text while preserving model-continuation boundaries."""

    all_chunks: list[str] = field(default_factory=list)
    current_chunks: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)

    def add(self, text: str) -> None:
        self.all_chunks.append(text)
        self.current_chunks.append(text)

    def boundary(self) -> None:
        segment = "".join(self.current_chunks).strip()
        if segment:
            self.segments.append(segment)
        self.current_chunks.clear()

    @property
    def final_text(self) -> str:
        return "".join(self.all_chunks)


class _FixtureTool:
    """agent_eval case 专用固定结果工具。"""

    def __init__(self, fixture: FakeTool) -> None:
        self.name = fixture.name
        if fixture.name == "web_search":
            self.description = WebSearchTool.description
            self.parameters_schema = WebSearchTool.parameters_schema
        elif fixture.name == "web_fetch":
            self.description = WebFetchTool.description
            self.parameters_schema = WebFetchTool.parameters_schema
        else:
            self.description = "读取完成当前请求所需的数据。"
            self.parameters_schema = {"type": "object", "properties": {}}
        self._fixture = fixture

    def invoke(self, _args: dict[str, Any]) -> ToolResult:
        meta = {"source_urls": self._fixture.source_urls} if self._fixture.source_urls else {}
        return ToolResult(
            text=self._fixture.result_text,
            is_error=self._fixture.is_error,
            meta=meta,
        )


def _build_manager(
    store: JsonlSessionStore,
    spec: ProviderSpec,
    *,
    tavily_api_key: str | None = None,
    fake_tools: list[FakeTool] | None = None,
    attachment_store: AttachmentStore | None = None,
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
    if fake_tools:
        fixture_names = {fixture.name for fixture in fake_tools}
        tools = [tool for tool in registry.all_tools() if tool.name not in fixture_names]
        tools.extend(cast(Any, _FixtureTool(fixture)) for fixture in fake_tools)
        registry = ToolRegistry(tools)

    return SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=default_context_manager,
        tool_registry=registry,
        metadata_store=metadata_store,
        message_renderer=(
            MultimodalInputAdapter(
                open_asset=attachment_store.open,
                cache_dir=attachment_store.root / "provider-cache",
            ).render_message
            if attachment_store is not None
            else None
        ),
    )


def _event_payload(msg: FixtureMessage, resolve: ResolveAttachments | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": msg.text}
    if msg.attachments:
        if resolve is None:
            raise ValueError("fixture attachments require an attachment resolver")
        payload["attachments"] = [item.to_dict() for item in resolve(msg.attachments)]
    return payload


def _inject_fixture(
    store: JsonlSessionStore,
    fixture: FixtureSession,
    model: str,
    *,
    resolve_attachments: ResolveAttachments | None = None,
) -> None:
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
                payload=_event_payload(msg, resolve_attachments),
                meta={},
            )
        )
    store.create(session)


def _inject_current_session_messages(
    store: JsonlSessionStore,
    session: Session,
    messages: list[FixtureMessage],
    *,
    resolve_attachments: ResolveAttachments | None = None,
) -> None:
    """把前置轮次注入当前被测会话，不触发 LLM。"""
    _inject_current_session_events(
        store,
        session,
        list(messages),
        resolve_attachments=resolve_attachments,
    )


def _inject_current_session_events(
    store: JsonlSessionStore,
    session: Session,
    events: list[FixtureCurrentEvent],
    *,
    resolve_attachments: ResolveAttachments | None = None,
) -> None:
    """Inject completed message/tool history into the active session without an LLM call."""
    now = datetime.now(UTC)
    for index, item in enumerate(events):
        payload: dict[str, Any]
        if isinstance(item, FixtureMessage):
            event_type: EventType = "user_message" if item.role == "user" else "assistant_message"
            payload = _event_payload(item, resolve_attachments)
            if event_type == "assistant_message":
                payload["partial"] = False
        elif isinstance(item, FixtureToolCallRequest):
            event_type = "tool_call_request"
            payload = {
                "tool_call_id": item.tool_call_id,
                "event_tool_call_id": item.tool_call_id,
                "tool_name": item.tool_name,
                "args": dict(item.args),
            }
        elif isinstance(item, FixtureToolCallResult):
            event_type = "tool_call_result"
            payload = {
                "tool_call_id": item.tool_call_id,
                "event_tool_call_id": item.tool_call_id,
                "tool_name": item.tool_name,
                "content": item.content,
                "is_error": item.is_error,
            }
        else:  # pragma: no cover - closed union, defensive for hand-built cases
            raise TypeError(f"unsupported current-session fixture event: {type(item).__name__}")
        event = Event(
            type=event_type,
            uuid=str(uuid4()),
            ts=now + timedelta(milliseconds=index),
            payload=payload,
            meta={},
        )
        session.append(event)
        store.append_event(session.session_id, event)


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    yield path.read_bytes()


def _import_fixture_attachment(
    store: AttachmentStore,
    attachment: FixtureAttachment,
) -> AttachmentRef:
    draft_id, _ = asyncio.run(
        store.import_stream(
            _file_chunks(attachment.path),
            display_name=attachment.display_name,
        )
    )
    return store.consume_draft(draft_id)


def run_case(case: Case, spec: ProviderSpec, *, tavily_api_key: str | None = None) -> Trace:
    """跑一个 case，返回完整 Trace；异常以 ``Trace.error`` 形式返回。"""
    try:
        with tempfile.TemporaryDirectory(prefix="agent-eval-") as tmp:
            temp_root = Path(tmp)
            store = JsonlSessionStore(temp_root / "sessions")
            attachment_store = AttachmentStore(temp_root / "attachments")
            attachment_cache: dict[tuple[Path, str], AttachmentRef] = {}

            def resolve_attachments(items: list[FixtureAttachment]) -> list[AttachmentRef]:
                resolved: list[AttachmentRef] = []
                for item in items:
                    key = (item.path, item.display_name)
                    ref = attachment_cache.get(key)
                    if ref is None:
                        ref = _import_fixture_attachment(attachment_store, item)
                        attachment_cache[key] = ref
                    resolved.append(ref)
                return resolved

            if case.fixture_session is not None:
                _inject_fixture(
                    store,
                    case.fixture_session,
                    spec.model,
                    resolve_attachments=resolve_attachments,
                )

            manager = _build_manager(
                store,
                spec,
                tavily_api_key=tavily_api_key,
                fake_tools=case.fake_tools,
                attachment_store=attachment_store,
            )
            session = manager.create(
                persona="default",
                model=spec.model,
                persona_id=BUILTIN_DEFAULT_PERSONA_ID,
            )
            _inject_current_session_messages(
                store,
                session,
                case.current_session_messages,
                resolve_attachments=resolve_attachments,
            )
            _inject_current_session_events(
                store,
                session,
                case.current_session_events,
                resolve_attachments=resolve_attachments,
            )

            def tool_run_context_factory(user_input: str) -> ToolRunContext:
                return ToolRunContext.create(user_input, now=case.runtime_now)

            conversation = manager.start_conversation(
                session,
                tool_run_context_factory=tool_run_context_factory,
            )

            tool_requests: dict[str, ToolCallRequest] = {}
            tool_calls: list[ToolCallTrace] = []
            assistant_text = _AssistantTextTrace()
            stop_reason = "unknown"

            for event in conversation.stream(
                case.query,
                attachments=resolve_attachments(case.query_attachments),
                surface=InteractionSurfaceId(case.surface) if case.surface is not None else None,
            ):
                if isinstance(event, ToolCallRequest):
                    assistant_text.boundary()
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
                    assistant_text.add(event.text)
                elif isinstance(event, TurnDone):
                    stop_reason = event.stop_reason

            assistant_text.boundary()

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
                final_text=assistant_text.final_text,
                stop_reason=stop_reason,
                assistant_segments=assistant_text.segments,
            )
    except Exception as exc:
        return Trace(
            case_id=case.id,
            tool_calls=[],
            final_text="",
            stop_reason="error",
            assistant_segments=[],
            error=f"{type(exc).__name__}: {exc}",
        )
