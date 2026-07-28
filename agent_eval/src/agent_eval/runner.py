"""跑单条 case 并收集 agent 行为 trace。"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

from agent.tool_runtime import ToolRunContext

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
    FixtureMessage,
    FixtureSession,
    ToolCallTrace,
    Trace,
)

ResolveAttachments = Callable[[list[FixtureAttachment]], list[AttachmentRef]]


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
    now = datetime.now(UTC)
    for index, msg in enumerate(messages):
        event_type: EventType = "user_message" if msg.role == "user" else "assistant_message"
        event = Event(
            type=event_type,
            uuid=str(uuid4()),
            ts=now + timedelta(milliseconds=index),
            payload=_event_payload(msg, resolve_attachments),
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
                fake_tool=case.fake_tool,
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

            def tool_run_context_factory(user_input: str) -> ToolRunContext:
                return ToolRunContext.create(user_input, now=case.runtime_now)

            conversation = manager.start_conversation(
                session,
                tool_run_context_factory=tool_run_context_factory,
            )

            tool_requests: dict[str, ToolCallRequest] = {}
            tool_calls: list[ToolCallTrace] = []
            text_buf: list[str] = []
            stop_reason = "unknown"

            for event in conversation.stream(
                case.query,
                attachments=resolve_attachments(case.query_attachments),
            ):
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
