"""跑单条 case 并收集 agent 行为 trace。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from agent.command_runtime.tools import ExecCommandTool
from agent.system_prompt import InteractionSurfaceId
from agent.tool_runtime import ToolRunContext
from agent.tools.builtin.skills import SkillReadTool
from agent.tools.builtin.social_platforms import SocialReadTool, SocialSearchTool
from agent.tools.builtin.todo_manage import TodoManageTool
from agent.tools.builtin.weather_query import WeatherQueryTool
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
    SkillActivationStatus,
    SkillCatalogService,
    TextDelta,
    Tool,
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
    FakeToolResponse,
    FixtureAttachment,
    FixtureCompaction,
    FixtureCurrentEvent,
    FixtureMessage,
    FixtureSession,
    FixtureSkill,
    FixtureToolCallRequest,
    FixtureToolCallResult,
    SkillActivationTrace,
    SkillCatalogEntryObservation,
    SkillCatalogObservation,
    ToolCallTrace,
    Trace,
)

ResolveAttachments = Callable[[list[FixtureAttachment]], list[AttachmentRef]]

_CATALOG_ENTRY_RE = re.compile(
    r"^- (?P<name>[a-z0-9]+(?:-[a-z0-9]+)*): .*?"
    r"\(source=(?P<source>builtin|personal|shared), skill_id=(?P<id>[^,]+),",
    re.MULTILINE,
)
_CATALOG_COVERAGE_RE = re.compile(r'<catalog_coverage entries="(?P<coverage>complete|truncated)"')


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


def _catalog_entries_from_content(content: str) -> list[SkillCatalogEntryObservation]:
    return [
        SkillCatalogEntryObservation(
            name=match.group("name"),
            source=match.group("source"),
            id=match.group("id"),
        )
        for match in _CATALOG_ENTRY_RE.finditer(content)
    ]


def _catalog_coverage_from_content(content: str) -> str:
    match = _CATALOG_COVERAGE_RE.search(content)
    if match is None:
        raise ValueError("Skill catalog is missing catalog_coverage")
    return match.group("coverage")


class _SkillCatalogRequestGuard:
    """Verify discovery truth at the snapshot and final provider-request boundaries."""

    def __init__(self, case: Case) -> None:
        self._case = case
        self._snapshot_entries: list[SkillCatalogEntryObservation] = []
        self._snapshot_coverage: str | None = None
        self._catalog_fingerprint: str | None = None
        self._render_fingerprint: str | None = None
        self._render_content_fingerprint: str | None = None
        self._request_entries: list[SkillCatalogEntryObservation] = []
        self._request_coverage: str | None = None
        self._request_content_fingerprint: str | None = None
        self._request_message_index: int | None = None
        self._preflight_passed = False

    def prepare(self, service: SkillCatalogService, *, model_window: int) -> None:
        snapshot = service.begin_turn(model_window=model_window)
        rendered = snapshot.rendered_catalog
        self._snapshot_entries = [
            SkillCatalogEntryObservation(name=item.name, source=item.source, id=item.id)
            for item in snapshot.catalog.entries
        ]
        self._snapshot_coverage = "truncated" if rendered.omitted_count else "complete"
        self._catalog_fingerprint = snapshot.catalog.fingerprint
        self._render_fingerprint = rendered.fingerprint
        self._render_content_fingerprint = hashlib.sha256(rendered.content.encode()).hexdigest()
        self._validate_catalog(
            self._snapshot_entries,
            coverage=self._snapshot_coverage,
            boundary="discovery snapshot",
        )

    def observe(self, messages: list[dict[str, Any]]) -> None:
        if self._request_message_index is not None:
            return
        catalog_messages = [
            (index, content)
            for index, message in enumerate(messages)
            if isinstance((content := message.get("content")), str)
            and "<skills_catalog>" in content
        ]
        if not catalog_messages:
            raise ValueError("LLM request is missing the production Skill catalog")
        index, content = catalog_messages[-1]
        self._request_message_index = index
        self._request_entries = _catalog_entries_from_content(content)
        self._request_coverage = _catalog_coverage_from_content(content)
        self._request_content_fingerprint = hashlib.sha256(content.encode()).hexdigest()
        if self._render_content_fingerprint != self._request_content_fingerprint:
            raise ValueError(
                "LLM request Skill catalog does not match the prepared production render"
            )
        self._validate_catalog(
            self._request_entries,
            coverage=self._request_coverage,
            boundary="LLM request",
        )
        self._preflight_passed = True

    def _validate_catalog(
        self,
        entries: list[SkillCatalogEntryObservation],
        *,
        coverage: str,
        boundary: str,
    ) -> None:
        expected_coverage = self._case.expected_skill_catalog_coverage
        if expected_coverage is not None and coverage != expected_coverage:
            raise ValueError(
                f"{boundary} Skill catalog coverage mismatch: "
                f"expected={expected_coverage} actual={coverage}"
            )

        actual_pairs = {(entry.name, entry.source) for entry in entries}
        missing_pairs = [
            (entry.name, entry.source)
            for entry in self._case.expected_skill_catalog_entries
            if (entry.name, entry.source) not in actual_pairs
        ]
        if missing_pairs:
            raise ValueError(
                f"{boundary} Skill catalog is missing expected entries: {missing_pairs}"
            )

        actual_names = {entry.name for entry in entries}
        judge = self._case.judge
        required_names = set(judge.must_recognize_catalog_skills if judge else [])
        missing_names = sorted(required_names - actual_names)
        if missing_names:
            raise ValueError(
                f"{boundary} Skill catalog is missing judge-visible Skills: {missing_names}"
            )

        expected_absent = set(self._case.expected_skill_catalog_absent)
        if judge is not None:
            expected_absent.update(judge.must_report_catalog_skills_undiscoverable)
        if expected_absent:
            if coverage != "complete":
                raise ValueError(f"{boundary} cannot prove Skill absence from a truncated catalog")
            unexpectedly_present = sorted(expected_absent & actual_names)
            if unexpectedly_present:
                raise ValueError(
                    f"{boundary} Skill catalog unexpectedly contains: {unexpectedly_present}"
                )
        if judge is not None and judge.must_cover_visible_skill_catalog and coverage != "complete":
            raise ValueError(
                f"{boundary} cannot require complete enumeration from a truncated catalog"
            )

    @property
    def observation(self) -> SkillCatalogObservation | None:
        if self._snapshot_coverage is None:
            return None
        assert self._catalog_fingerprint is not None
        assert self._render_fingerprint is not None
        return SkillCatalogObservation(
            snapshot_entries=list(self._snapshot_entries),
            snapshot_coverage=self._snapshot_coverage,
            catalog_fingerprint=self._catalog_fingerprint,
            render_fingerprint=self._render_fingerprint,
            request_entries=list(self._request_entries),
            request_coverage=self._request_coverage,
            request_content_fingerprint=self._request_content_fingerprint,
            request_message_index=self._request_message_index,
            preflight_passed=self._preflight_passed,
        )


class _CatalogGuardedLLMClient:
    """Eval-only proxy that checks final messages before delegating a real request."""

    def __init__(self, delegate: LLMClient, guard: _SkillCatalogRequestGuard) -> None:
        self._delegate = delegate
        self._guard = guard

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def stream(self, messages: list[dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
        self._guard.observe(messages)
        return self._delegate.stream(messages, *args, **kwargs)


class _FixtureTool:
    """agent_eval case 专用固定结果工具。"""

    def __init__(self, fixture: FakeTool, template: Tool | None = None) -> None:
        self.name = fixture.name
        if template is not None:
            self.description = template.description
            self.parameters_schema = template.parameters_schema
        elif fixture.name == "web_search":
            self.description = WebSearchTool.description
            self.parameters_schema = WebSearchTool.parameters_schema
        elif fixture.name == "web_fetch":
            self.description = WebFetchTool.description
            self.parameters_schema = WebFetchTool.parameters_schema
        elif fixture.name == "social_search":
            self.description = SocialSearchTool.description
            self.parameters_schema = SocialSearchTool.parameters_schema
        elif fixture.name == "social_read":
            self.description = SocialReadTool.description
            self.parameters_schema = SocialReadTool.parameters_schema
        elif fixture.name == "weather_query":
            self.description = WeatherQueryTool.description
            self.parameters_schema = WeatherQueryTool.parameters_schema
        elif fixture.name == "todo_manage":
            self.description = TodoManageTool.description
            self.parameters_schema = TodoManageTool.parameters_schema
        elif fixture.name == "skill_read":
            self.description = SkillReadTool.description
            self.parameters_schema = SkillReadTool.parameters_schema
        elif fixture.name == "exec_command":
            self.description = ExecCommandTool.description
            self.parameters_schema = ExecCommandTool.parameters_schema
        else:
            self.description = "读取完成当前请求所需的数据。"
            self.parameters_schema = {"type": "object", "properties": {}}
        self._fixture = fixture

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        selected: FakeTool | FakeToolResponse = self._fixture
        if self._fixture.responses:
            matches = [
                response
                for response in self._fixture.responses
                if _value_contains(args, response.match_args)
            ]
            if len(matches) != 1:
                kind = "fixture_no_match" if not matches else "fixture_ambiguous_match"
                return ToolResult(
                    text=(f"评测 fixture 没有唯一匹配本次 {self.name} 调用参数的固定结果。"),
                    is_error=True,
                    meta={"error_kind": kind, "matching_response_count": len(matches)},
                )
            selected = matches[0]
        meta = {"source_urls": selected.source_urls} if selected.source_urls else {}
        return ToolResult(
            text=selected.result_text,
            is_error=selected.is_error,
            meta=meta,
        )


def _value_contains(actual: object, expected: object) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _value_contains(actual[key], value) for key, value in expected.items()
        )
    return actual == expected


def _build_manager(
    store: JsonlSessionStore,
    spec: ProviderSpec,
    *,
    tavily_api_key: str | None = None,
    fake_tools: list[FakeTool] | None = None,
    skill_service: SkillCatalogService | None = None,
    attachment_store: AttachmentStore | None = None,
    skill_catalog_request_guard: _SkillCatalogRequestGuard | None = None,
    self_presentation_mode: str = "immersive",
) -> SessionManager:
    """装配一个挂指定 store 的 SessionManager。"""

    def _llm_factory(_model: str) -> LLMClient:
        client = LLMClient(spec)
        if skill_catalog_request_guard is None:
            return client
        return cast(LLMClient, _CatalogGuardedLLMClient(client, skill_catalog_request_guard))

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(
            persona_id=persona_id,
            self_presentation_mode_provider=lambda: cast(Any, self_presentation_mode),
        )

    metadata_store = SessionMetadataStore(store.base_dir / "metadata.json")
    session_catalog = SessionCatalog(store, metadata_store)
    registry = make_default_registry(
        tavily_api_key=tavily_api_key,
        session_store=store,
        session_catalog=session_catalog,
        stderr_warn=False,
        skill_service=skill_service,
    )
    if fake_tools:
        fixture_names = {fixture.name for fixture in fake_tools}
        templates = {tool.name: tool for tool in registry.all_tools()}
        tools = [tool for tool in registry.all_tools() if tool.name not in fixture_names]
        tools.extend(
            cast(Any, _FixtureTool(fixture, templates.get(fixture.name))) for fixture in fake_tools
        )
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
        skill_service=skill_service,
    )


def _materialize_fixture_skills(root: Path, fixtures: list[FixtureSkill]) -> None:
    """Write case-local packages beneath one stable production discovery root."""
    for fixture in fixtures:
        package = root / fixture.source / fixture.name
        if fixture.package_path is not None:
            shutil.copytree(fixture.package_path, package)
            continue
        if (
            fixture.description is None
            or fixture.instructions is None
            or fixture.allow_implicit_invocation is None
        ):
            raise ValueError(f"inline fixture Skill {fixture.name!r} 缺少必要内容")
        package.mkdir(parents=True, exist_ok=False)
        package.joinpath("SKILL.md").write_text(
            "---\n"
            f"name: {json.dumps(fixture.name, ensure_ascii=False)}\n"
            f"description: {json.dumps(fixture.description, ensure_ascii=False)}\n"
            "---\n\n"
            f"{fixture.instructions.rstrip()}\n",
            encoding="utf-8",
        )
        agents_dir = package / "agents"
        agents_dir.mkdir()
        agents_dir.joinpath("openai.yaml").write_text(
            "policy:\n"
            "  allow_implicit_invocation: "
            f"{'true' if fixture.allow_implicit_invocation else 'false'}\n",
            encoding="utf-8",
        )


def _replace_fixture_skills(
    root: Path,
    previous: list[FixtureSkill],
    current: list[FixtureSkill],
) -> None:
    """Transition fixture packages in place so unchanged Skills keep stable identity."""

    for fixture in previous:
        package = root / fixture.source / fixture.name
        if package.exists():
            shutil.rmtree(package)
    _materialize_fixture_skills(root, current)


def _build_fixture_skill_service(root: Path, fixtures: list[FixtureSkill]) -> SkillCatalogService:
    """Materialize case-local packages, then let the production service discover them."""

    _materialize_fixture_skills(root, fixtures)
    personal_root = root / "personal"
    service = SkillCatalogService(
        personal_root=personal_root,
        shared_root=root / "shared",
        watcher_enabled=False,
    )
    service.initialize()
    return service


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


def _inject_fixture_compaction(
    store: JsonlSessionStore,
    session: Session,
    fixture: FixtureCompaction,
) -> None:
    """Append a valid production-shaped checkpoint over active projected messages."""
    messages = session.messages
    index = fixture.covered_through_message - 1
    if index >= len(messages):
        raise ValueError(
            "fixture_compaction.covered_through_message exceeds active message count: "
            f"{fixture.covered_through_message} > {len(messages)}"
        )
    covered = messages[index]
    retained_from = messages[index + 1].uuid if index + 1 < len(messages) else ""
    event = Event(
        type="compaction",
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload={
            "summary": fixture.summary,
            "covered_through_uuid": covered.uuid,
            "checkpoint_version": 2,
            "summary_prompt_version": 1,
            "summary_source_from_uuid": messages[0].uuid,
            "retained_from_uuid": retained_from,
            "omitted_before_count": 0,
            "model": session.current_model,
        },
        meta={},
    )
    session.append(event)
    store.append_event(session.session_id, event)


def _inject_skill_catalog_state(
    store: JsonlSessionStore,
    session: Session,
    service: SkillCatalogService,
    *,
    model_window: int,
) -> None:
    """Inject one trusted historical production-rendered Skill catalog snapshot."""

    snapshot = service.begin_turn(model_window=model_window)
    event = Event(
        type="skill_catalog_state",
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload={
            "generation": snapshot.catalog.generation,
            "render_fingerprint": snapshot.rendered_catalog.fingerprint,
            "catalog_fingerprint": snapshot.catalog.fingerprint,
            "budget_profile": snapshot.rendered_catalog.budget_profile,
            "content": snapshot.rendered_catalog.content,
        },
        meta={"model": session.current_model},
    )
    session.append(event)
    store.append_event(session.session_id, event)


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
    catalog_guard: _SkillCatalogRequestGuard | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="agent-eval-") as tmp:
            temp_root = Path(tmp)
            store = JsonlSessionStore(temp_root / "sessions")
            attachment_store = AttachmentStore(temp_root / "attachments")
            attachment_cache: dict[tuple[Path, str], AttachmentRef] = {}
            needs_skill_catalog = bool(
                case.fixture_skills
                or case.prior_fixture_skills
                or case.expected_skill_catalog_coverage
                or case.expected_skill_catalog_entries
                or case.expected_skill_catalog_absent
                or (
                    case.judge is not None
                    and (
                        case.judge.must_recognize_catalog_skills
                        or case.judge.must_report_catalog_skills_undiscoverable
                        or case.judge.must_cover_visible_skill_catalog
                    )
                )
            )
            fixture_skill_root = temp_root / "fixture-skills"
            initial_fixture_skills = case.prior_fixture_skills or case.fixture_skills
            skill_service = (
                _build_fixture_skill_service(
                    fixture_skill_root,
                    initial_fixture_skills,
                )
                if needs_skill_catalog
                else None
            )
            catalog_model_window = case.fixture_skill_catalog_window
            if needs_skill_catalog and catalog_model_window is None:
                catalog_model_window = LLMClient(spec).context_window

            catalog_guard = _SkillCatalogRequestGuard(case) if needs_skill_catalog else None

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
                skill_service=skill_service,
                attachment_store=attachment_store,
                skill_catalog_request_guard=catalog_guard,
                self_presentation_mode=case.self_presentation_mode,
            )
            session = manager.create(
                persona="default",
                model=spec.model,
                persona_id=BUILTIN_DEFAULT_PERSONA_ID,
            )
            if case.prior_fixture_skills:
                assert catalog_model_window is not None
                assert skill_service is not None
                _inject_skill_catalog_state(
                    store,
                    session,
                    skill_service,
                    model_window=catalog_model_window,
                )
                _replace_fixture_skills(
                    fixture_skill_root,
                    case.prior_fixture_skills,
                    case.fixture_skills,
                )
                skill_service.refresh()
            if catalog_guard is not None:
                assert skill_service is not None
                assert catalog_model_window is not None
                catalog_guard.prepare(skill_service, model_window=catalog_model_window)
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
            if case.fixture_compaction is not None:
                _inject_fixture_compaction(store, session, case.fixture_compaction)

            def tool_run_context_factory(user_input: str) -> ToolRunContext:
                return ToolRunContext.create(user_input, now=case.runtime_now)

            conversation = manager.start_conversation(
                session,
                tool_run_context_factory=tool_run_context_factory,
            )

            tool_requests: dict[str, ToolCallRequest] = {}
            tool_calls: list[ToolCallTrace] = []
            skill_activations: list[SkillActivationTrace] = []
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
                elif isinstance(event, SkillActivationStatus):
                    skill_activations.append(
                        SkillActivationTrace(
                            status=event.status,
                            skill_id=event.skill_id,
                            name=event.name,
                            source=event.source,
                            invocation_kind=event.invocation_kind,
                            reason=event.reason,
                            error_code=event.error_code,
                            error_message=event.error_message,
                        )
                    )
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
                skill_activations=skill_activations,
                skill_catalog_observation=(catalog_guard.observation if catalog_guard else None),
            )
    except Exception as exc:
        return Trace(
            case_id=case.id,
            tool_calls=[],
            final_text="",
            stop_reason="error",
            assistant_segments=[],
            skill_activations=[],
            error=f"{type(exc).__name__}: {exc}",
            skill_catalog_observation=(catalog_guard.observation if catalog_guard else None),
        )
