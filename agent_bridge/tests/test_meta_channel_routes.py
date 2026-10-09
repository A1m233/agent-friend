"""007 起 agent_bridge 新增 ``POST /v1/sessions`` + ``POST /v1/sessions/{id}/channel``
两个端点的集成测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agent.scheduled_tasks import ScheduledTaskService, ScheduledTaskStatus, ScheduledTaskStore
from agent.scheduling import OnceSchedule
from agent.sessions.query import CancelCheck
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    AttachmentStore,
    Conversation,
    Event,
    EventType,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    SessionMetadataStore,
    SessionNotFoundError,
    make_default_registry,
)
from llm_providers import (
    LanguageModel,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ModelRuntimeConfig,
)


class _RouteLLM:
    seen_messages: ClassVar[list[list[dict[str, Any]]]] = []
    seen_tools: ClassVar[list[list[dict[str, Any]] | None]] = []
    context_window = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        self.seen_messages.append(request.messages)
        self.seen_tools.append(request.tools)
        yield LLMTextDelta(text="new answer")
        yield LLMTurnDone(stop_reason="end_turn")


@pytest.fixture
def runtime(tmp_path: Path) -> BridgeRuntime:
    """构造一个全新 BridgeRuntime，sessions 落到 tmp_path 隔离。"""
    _RouteLLM.seen_messages = []
    _RouteLLM.seen_tools = []
    settings = BridgeSettings(
        sessions_dir=tmp_path / "sessions",
        attachments_dir=tmp_path / "attachments",
    )
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()

    def _llm_factory(model: ModelRuntimeConfig) -> LanguageModel:
        return _RouteLLM()

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    persistent_session_manager = SessionManager(
        store=persistent_store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
        metadata_store=SessionMetadataStore(settings.sessions_dir / "metadata.json"),
    )

    return BridgeRuntime(
        settings=settings,
        persistent_store=persistent_store,
        transient_store=persistent_store,  # 测试用同一个就行
        persistent_session_manager=persistent_session_manager,
        catalog=catalog,
        tool_registry=tool_registry,
        prompt_builder_factory=_prompt_factory,
        default_persona=catalog.find_by_name("default").name,
        default_model="deepseek/deepseek-chat",
        attachment_store=AttachmentStore(settings.attachments_dir),
    )


@pytest.fixture
def client(runtime: BridgeRuntime) -> TestClient:
    app = create_app_with_runtime(runtime)
    return TestClient(app)


# ===== POST /v1/sessions =====


@pytest.mark.parametrize("delete", [False, True])
def test_target_lifecycle_stops_tasks_without_resuming_on_unarchive(
    runtime: BridgeRuntime, tmp_path: Path, delete: bool
) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    service = ScheduledTaskService(store)
    runtime = replace(runtime, scheduled_task_store=store, scheduled_task_service=service)
    client = TestClient(create_app_with_runtime(runtime))
    target = client.post("/v1/sessions", json={}).json()["session_id"]
    task = service.create(
        name="target-bound",
        instruction="hello",
        target_session_id=target,
        schedule=OnceSchedule((datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )
    response = (
        client.delete(f"/v1/sessions/{target}")
        if delete
        else client.patch(f"/v1/sessions/{target}", json={"archived": True})
    )
    assert response.status_code == 200
    stopped = service.get(task.id)
    assert stopped.status is ScheduledTaskStatus.PAUSED
    assert stopped.pause_reason == (
        "target_session_missing" if delete else "target_session_archived"
    )
    assert stopped.next_fire_at_utc is None
    if not delete:
        assert client.patch(f"/v1/sessions/{target}", json={"archived": False}).status_code == 200
        assert service.get(task.id).status is ScheduledTaskStatus.PAUSED


class TestCreateSessionEndpoint:
    def test_default_creates_text_channel(self, client: TestClient) -> None:
        resp = client.post("/v1/sessions", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"]
        assert data["channel"] == "text"
        assert data["persona"]
        assert data["model"]

    def test_voice_channel_persists_to_session_meta(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        resp = client.post("/v1/sessions", json={"channel": "voice"})
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        # 直接通过 store 重新读 session，确认 initial_channel 落盘
        loaded = runtime.persistent_session_manager.open(sid)
        assert loaded.current_channel == "voice"
        assert loaded.events[0].payload.get("initial_channel") == "voice"

    def test_persona_argument(self, client: TestClient) -> None:
        resp = client.post("/v1/sessions", json={"persona": "default"})
        assert resp.status_code == 200
        assert resp.json()["persona"]

    def test_unknown_persona_returns_400(self, client: TestClient) -> None:
        resp = client.post("/v1/sessions", json={"persona": "totally_made_up"})
        assert resp.status_code == 400


# ===== GET /v1/sessions/{id} =====


class TestGetSessionEndpoint:
    @pytest.mark.parametrize("view", ["full", "active"])
    @pytest.mark.parametrize("changed_during_read", [False, True])
    def test_detail_reuses_loaded_session_and_rechecks_revision(
        self,
        client: TestClient,
        runtime: BridgeRuntime,
        monkeypatch: pytest.MonkeyPatch,
        view: str,
        changed_during_read: bool,
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        metadata = SessionMetadataStore(runtime.settings.sessions_dir / "metadata.json")
        metadata.compare_and_set_approval_mode(sid, expected_version=0, mode="session_auto")
        manager = runtime.persistent_session_manager
        original_read = manager.query if view == "active" else manager.open
        reads = 0
        revision = 0

        def read(session_id: str, *, cancel_check: CancelCheck = None) -> object:
            nonlocal reads, revision
            reads += 1
            snapshot = original_read(session_id, cancel_check=cancel_check)
            if changed_during_read and reads == 1:
                runtime.persistent_store.append_event(
                    session_id,
                    Event(
                        type="user_message",
                        uuid="concurrent-message",
                        ts=datetime.now(UTC),
                        payload={"content": "synthetic concurrent append"},
                    ),
                )
                revision += 1
            return snapshot

        monkeypatch.setattr(manager, "query" if view == "active" else "open", read)
        monkeypatch.setattr(
            runtime.persistent_session_manager, "current_revision", lambda _id: revision
        )

        response = client.get(f"/v1/sessions/{sid}?view={view}")

        assert response.status_code == 200
        assert reads == (2 if changed_during_read else 1)
        detail = response.json()
        assert detail["revision"] == revision
        assert detail["approval_mode"] == "session_auto"
        assert detail["approval_mode_version"] == 1
        assert any(ev["uuid"] == "concurrent-message" for ev in detail["active_events"]) == (
            changed_during_read
        )
        assert ("events" in detail) == (view == "full")

    def test_full_view_remains_backward_compatible(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.get(f"/v1/sessions/{sid}", headers={"accept-encoding": "identity"})

        assert resp.status_code == 200
        detail = resp.json()
        assert detail["session_id"] == sid
        assert detail["events"]
        assert detail["active_events"] == detail["events"]

    def test_active_view_omits_raw_events(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.get(
            f"/v1/sessions/{sid}?view=active",
            headers={"accept-encoding": "identity"},
        )

        assert resp.status_code == 200
        detail = resp.json()
        assert detail["session_id"] == sid
        assert detail["active_events"]
        assert "events" not in detail

    def test_provider_reasoning_state_is_redacted_from_rest(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        runtime.persistent_store.append_event(
            sid,
            Event(
                type="provider_reasoning_state",
                uuid="reasoning-state",
                ts=datetime.now(UTC),
                payload={
                    "provider": "deepseek",
                    "model": "deepseek/deepseek-flash",
                    "format": "text",
                    "replayScope": "conversation",
                    "data": "private-reasoning-continuation",
                },
            ),
        )

        response = client.get(f"/v1/sessions/{sid}")

        assert response.status_code == 200
        assert "private-reasoning-continuation" not in response.text
        reasoning_event = next(
            event
            for event in response.json()["events"]
            if event["type"] == "provider_reasoning_state"
        )
        assert reasoning_event["payload"] == {
            "provider": "deepseek",
            "model": "deepseek/deepseek-flash",
            "format": "text",
            "replayScope": "conversation",
            "dataCharCount": len("private-reasoning-continuation"),
            "redacted": True,
        }

    def test_unknown_view_returns_422(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.get(f"/v1/sessions/{sid}?view=unknown")

        assert resp.status_code == 422

    def test_large_json_response_supports_gzip(
        self,
        client: TestClient,
        runtime: BridgeRuntime,
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        runtime.persistent_store.append_event(
            sid,
            Event(
                type="assistant_message",
                uuid="large-assistant-message",
                ts=datetime.now(UTC),
                payload={"content": "可压缩的长会话内容" * 1000, "partial": False},
            ),
        )

        compressed = client.get(
            f"/v1/sessions/{sid}?view=active",
            headers={"accept-encoding": "gzip"},
        )
        identity = client.get(
            f"/v1/sessions/{sid}?view=active",
            headers={"accept-encoding": "identity"},
        )

        assert compressed.status_code == 200
        assert compressed.headers["content-encoding"] == "gzip"
        assert "Accept-Encoding" in compressed.headers["vary"]
        assert "content-encoding" not in identity.headers
        assert compressed.json() == identity.json()


# ===== DELETE /v1/sessions/{id} =====


class TestDeleteSessionEndpoint:
    def test_delete_session_removes_file(self, client: TestClient, runtime: BridgeRuntime) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        resp = client.delete(f"/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": sid, "deleted": True}

        with pytest.raises(SessionNotFoundError, match="会话不存在"):
            runtime.persistent_session_manager.open(sid)

    def test_delete_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.delete("/v1/sessions/nonexistent-uuid")
        assert resp.status_code == 404


# ===== PATCH /v1/sessions/{id} + archived listing/deletion =====


class TestSessionLifecycleEndpoint:
    def test_rename_overrides_list_title(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.patch(f"/v1/sessions/{sid}", json={"title": "新的标题"})

        assert resp.status_code == 200
        assert resp.json()["title"] == "新的标题"
        items = client.get("/v1/sessions").json()
        assert items[0]["session_id"] == sid
        assert items[0]["title"] == "新的标题"
        assert items[0]["archived_at"] is None

    def test_rename_empty_title_returns_400(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.patch(f"/v1/sessions/{sid}", json={"title": "   "})

        assert resp.status_code == 400

    def test_archive_moves_session_to_archived_list(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.patch(f"/v1/sessions/{sid}", json={"archived": True})

        assert resp.status_code == 200
        assert resp.json()["archived_at"]
        assert client.get("/v1/sessions").json() == []
        archived = client.get("/v1/sessions?archived=true").json()
        assert [item["session_id"] for item in archived] == [sid]

    def test_unarchive_returns_session_to_default_list(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        client.patch(f"/v1/sessions/{sid}", json={"archived": True})

        resp = client.patch(f"/v1/sessions/{sid}", json={"archived": False})

        assert resp.status_code == 200
        assert resp.json()["archived_at"] is None
        assert client.get("/v1/sessions?archived=true").json() == []
        assert [item["session_id"] for item in client.get("/v1/sessions").json()] == [sid]

    def test_archived_search_runs_on_backend_title(self, client: TestClient) -> None:
        first = client.post("/v1/sessions", json={}).json()["session_id"]
        second = client.post("/v1/sessions", json={}).json()["session_id"]
        client.patch(f"/v1/sessions/{first}", json={"title": "查 Codex 5.5 缓存时长"})
        client.patch(f"/v1/sessions/{first}", json={"archived": True})
        client.patch(f"/v1/sessions/{second}", json={"title": "别的聊天"})
        client.patch(f"/v1/sessions/{second}", json={"archived": True})

        resp = client.get("/v1/sessions?archived=true&q=codex")

        assert resp.status_code == 200
        assert [item["session_id"] for item in resp.json()] == [first]

    def test_delete_archived_removes_only_archived_sessions(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        archived_sid = client.post("/v1/sessions", json={}).json()["session_id"]
        normal_sid = client.post("/v1/sessions", json={}).json()["session_id"]
        client.patch(f"/v1/sessions/{archived_sid}", json={"archived": True})

        resp = client.delete("/v1/sessions/archived")

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1
        assert resp.json()["session_ids"] == [archived_sid]
        with pytest.raises(SessionNotFoundError, match="会话不存在"):
            runtime.persistent_session_manager.open(archived_sid)
        assert runtime.persistent_session_manager.open(normal_sid).session_id == normal_sid


# ===== POST /v1/sessions/{id}/edit-resend-latest =====


class TestEditResendLatestEndpoint:
    def test_runtime_edit_failure_is_durable_without_route_callback(
        self, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace

        from agent.context.errors import ContextCompactionUnavailableError
        from agent_bridge.agent_runtime_factory import build_agent_runtime

        def unavailable(self: _RouteLLM, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            raise ContextCompactionUnavailableError("unavailable")
            yield  # pragma: no cover

        monkeypatch.setattr(_RouteLLM, "stream", unavailable)
        engine = build_agent_runtime(
            settings=runtime.settings,
            session_manager=runtime.persistent_session_manager,
            memory=None,
            session_turn_guard=runtime.turn_coordinator.hold,
        )
        runtime = replace(runtime, agent_runtime=engine)
        client = TestClient(create_app_with_runtime(runtime))
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        runtime.persistent_store.append_event(
            sid,
            Event(
                type="user_message",
                uuid="old-user",
                ts=datetime.now(UTC),
                payload={"content": "old question"},
            ),
        )
        engine.start()
        try:
            response = client.post(
                f"/v1/sessions/{sid}/edit-resend-latest",
                json={
                    "runId": "failed-edit",
                    "text": "replacement",
                    "expectedUserContent": "old question",
                },
            )
            assert response.status_code == 200
            events = runtime.persistent_store.load(sid).active_events
            errors = [e for e in events if e.type == "run_error"]
            assert len(errors) == 1
            assert errors[0].payload["run_id"] == "failed-edit"
            assert errors[0].payload["message"]
            assert errors[0].payload["code"] == "context_compaction_unavailable"
            assert any(e.type == "run_terminal" and e.payload["status"] == "failed" for e in events)
            # Reload uses the same durable error, independent of the SSE consumer.
            detail = client.get(f"/v1/sessions/{sid}").json()
            assert sum(e["type"] == "run_error" for e in detail["active_events"]) == 1
        finally:
            engine.stop()

    def test_edit_resend_latest_streams_and_exposes_active_events(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        old_user = Event(
            type="user_message",
            uuid="old-user",
            ts=datetime.now(UTC),
            payload={"content": "old question"},
        )
        old_assistant = Event(
            type="assistant_message",
            uuid="old-assistant",
            ts=datetime.now(UTC),
            payload={"content": "old answer", "partial": False},
        )
        runtime.persistent_store.append_event(sid, old_user)
        runtime.persistent_store.append_event(sid, old_assistant)

        with client.stream(
            "POST",
            f"/v1/sessions/{sid}/edit-resend-latest",
            json={
                "runId": "edit-client-run",
                "text": "new question",
                "expectedUserContent": "old question",
            },
            headers={"accept": "text/event-stream"},
        ) as resp:
            body = "".join(resp.iter_text())

        assert resp.status_code == 200
        assert "content-encoding" not in resp.headers
        assert "new answer" in body
        assert "edit-client-run" in body

        detail = client.get(f"/v1/sessions/{sid}").json()
        assert [ev["type"] for ev in detail["events"]].count("turn_rewrite") == 1
        active_message_contents = [
            ev["payload"].get("content")
            for ev in detail["active_events"]
            if ev["type"] in {"user_message", "assistant_message"}
        ]
        assert active_message_contents == [
            "new question",
            "new answer",
        ]
        assert any(
            '<surface type="desktop_chat" />' in message.get("content", "")
            for message in _RouteLLM.seen_messages[-1]
        )

    def test_edit_resend_replaces_original_and_new_attachments_in_request_order(
        self, client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BridgeRuntime, "session_files_available", property(lambda _: True))
        sid = client.post(
            "/v1/sessions",
            json={"model": "kimi/kimi-k2.6"},
        ).json()["session_id"]
        assert runtime.attachment_store is not None
        old_draft_id, old_ref = asyncio.run(
            runtime.attachment_store.import_stream(
                _bytes(b"old attachment"),
                display_name="old.txt",
                declared_media_type="text/plain",
            )
        )
        runtime.attachment_store.consume_draft(old_draft_id)
        new_draft_id, new_ref = asyncio.run(
            runtime.attachment_store.import_stream(
                _bytes(b"new attachment"),
                display_name="new.txt",
                declared_media_type="text/plain",
            )
        )
        runtime.persistent_store.append_event(
            sid,
            Event(
                type="user_message",
                uuid="old-user-with-attachment",
                ts=datetime.now(UTC),
                payload={"content": "", "attachments": [old_ref.to_dict()]},
            ),
        )

        with client.stream(
            "POST",
            f"/v1/sessions/{sid}/edit-resend-latest",
            json={
                "text": "",
                "expectedUserMessageId": "old-user-with-attachment",
                "expectedUserContent": "",
                "attachments": [
                    {"source": "draft", "draftId": new_draft_id, "kind": "document"},
                    {"source": "existing", "assetId": old_ref.asset_id},
                ],
            },
            headers={"accept": "text/event-stream"},
        ) as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "new answer" in body
        active_user = next(
            event
            for event in reversed(runtime.persistent_session_manager.open(sid).active_events)
            if event.type == "user_message"
        )
        assert [item["asset_id"] for item in active_user.payload["attachments"]] == [
            new_ref.asset_id,
            old_ref.asset_id,
        ]

    def test_edit_resend_rejects_existing_asset_outside_target(
        self, client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BridgeRuntime, "session_files_available", property(lambda _: True))
        sid = client.post(
            "/v1/sessions",
            json={"model": "kimi/kimi-k2.6"},
        ).json()["session_id"]
        assert runtime.attachment_store is not None
        _, unrelated = asyncio.run(
            runtime.attachment_store.import_stream(
                _bytes(b"unrelated"),
                display_name="unrelated.txt",
                declared_media_type="text/plain",
            )
        )
        runtime.persistent_store.append_event(
            sid,
            Event(
                type="user_message",
                uuid="target-user",
                ts=datetime.now(UTC),
                payload={"content": "old"},
            ),
        )

        response = client.post(
            f"/v1/sessions/{sid}/edit-resend-latest",
            json={
                "text": "new",
                "expectedUserMessageId": "target-user",
                "attachments": [
                    {"source": "existing", "assetId": unrelated.asset_id},
                ],
            },
            headers={"accept": "text/event-stream"},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "attachment_not_on_edit_target"
        assert not any(
            event.type == "turn_rewrite"
            for event in runtime.persistent_session_manager.open(sid).events
        )

    def test_edit_resend_rejects_stale_target_before_sse(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        runtime.persistent_store.append_event(
            sid,
            Event(
                type="user_message",
                uuid="current-user",
                ts=datetime.now(UTC),
                payload={"content": "current"},
            ),
        )

        response = client.post(
            f"/v1/sessions/{sid}/edit-resend-latest",
            json={
                "text": "new",
                "expectedUserMessageId": "stale-user",
                "expectedUserContent": "current",
                "attachments": [],
            },
            headers={"accept": "text/event-stream"},
        )

        assert response.status_code == 409
        assert not response.headers["content-type"].startswith("text/event-stream")


async def _bytes(value: bytes) -> AsyncIterator[bytes]:
    yield value


class TestContinueToolResultEndpoint:
    def test_missing_session_returns_not_found_without_starting_a_run(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/v1/sessions/missing/continue-tool-result", json={"runId": "unused"}
        )
        assert response.status_code == 404
        assert not _RouteLLM.seen_messages

    def test_runtime_continuation_survives_lifecycle_events_and_rejects_stale_requests(
        self,
        client: TestClient,
        runtime: BridgeRuntime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event as ThreadEvent

        from agent.runtime import AgentRuntime

        entered, release = ThreadEvent(), ThreadEvent()
        original_stream = _RouteLLM.stream

        def blocked_stream(model: _RouteLLM, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            entered.set()
            assert release.wait(10), "test did not release the model"
            yield from original_stream(model, request)

        monkeypatch.setattr(_RouteLLM, "stream", blocked_stream)
        manager = runtime.persistent_session_manager
        engine = AgentRuntime(
            conversation_factory=lambda sid: manager.start_conversation(
                manager.open_for_execution(sid)
            ),
            session_turn_guard=runtime.turn_coordinator.hold,
        )
        from dataclasses import replace

        runtime = replace(runtime, agent_runtime=engine)
        client = TestClient(create_app_with_runtime(runtime))
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        seed: list[tuple[EventType, dict[str, Any]]] = [
            ("run_started", {"run_id": "failed", "started_at": datetime.now(UTC).isoformat()}),
            ("user_message", {"content": "finish the remaining work"}),
            ("run_error", {"run_id": "failed", "code": "upstream_transient", "recoverable": True}),
            ("run_terminal", {"run_id": "failed", "status": "failed"}),
        ]
        for kind, payload in seed:
            runtime.persistent_store.append_event(
                sid,
                Event(
                    type=kind,
                    uuid=f"old-{kind}",
                    ts=datetime.now(UTC),
                    payload=payload,
                    meta={"run_id": "failed"},
                ),
            )
        engine.start()
        try:
            before = client.get(f"/v1/sessions/{sid}?view=active").json()
            assert before["recovery"]["sourceRunId"] == "failed"
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    client.post,
                    f"/v1/sessions/{sid}/continue-tool-result",
                    json={"runId": "continued", "sourceRunId": "failed"},
                )
                try:
                    assert entered.wait(10)
                    for duplicate_id in ("continued", "competing"):
                        rejected = client.post(
                            f"/v1/sessions/{sid}/continue-tool-result",
                            json={"runId": duplicate_id, "sourceRunId": "failed"},
                        )
                        assert rejected.status_code == 409
                finally:
                    release.set()
                response = pending.result(timeout=10)
            assert response.status_code == 200
            assert "new answer" in response.text and '"type":"RUN_ERROR"' not in response.text
            snapshot = client.get("/v1/runs/continued").json()
            assert snapshot["outcome"] == "completed"
            assert snapshot["source_run_id"] == snapshot["root_run_id"] == "failed"
            after = client.get(f"/v1/sessions/{sid}?view=active").json()
            assert after["recovery"] is None
            assert sum(e["type"] == "user_message" for e in after["active_events"]) == 1
            start = next(
                e
                for e in after["active_events"]
                if e["type"] == "run_started" and e["payload"]["run_id"] == "continued"
            )
            assert start["payload"]["source_run_id"] == "failed"
            assert len(_RouteLLM.seen_messages) == 1
            assert engine.get_run_snapshot("competing") is None
            refused = client.post(
                f"/v1/sessions/{sid}/continue-tool-result",
                json={"runId": "stale", "sourceRunId": "failed"},
            )
            assert refused.status_code == 409
            assert len(manager.query(sid).active_events) == len(after["active_events"])
        finally:
            engine.stop()

    def test_continues_from_saved_result_with_normal_tools(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        events = [
            Event(
                type="user_message",
                uuid="user-1",
                ts=datetime.now(UTC),
                payload={"content": "查一下"},
            ),
            Event(
                type="assistant_message",
                uuid="assistant-tool",
                ts=datetime.now(UTC),
                payload={"content": "", "partial": False},
            ),
            Event(
                type="tool_call_request",
                uuid="tool-request",
                ts=datetime.now(UTC),
                payload={
                    "tool_call_id": "provider-call",
                    "event_tool_call_id": "tool-1",
                    "tool_name": "web_search",
                    "args": {"query": "latest"},
                },
            ),
            Event(
                type="tool_call_result",
                uuid="tool-result",
                ts=datetime.now(UTC),
                payload={
                    "tool_call_id": "provider-call",
                    "event_tool_call_id": "tool-1",
                    "tool_name": "web_search",
                    "content": "saved result",
                    "is_error": False,
                },
            ),
            Event(
                type="run_error",
                uuid="run-error",
                ts=datetime.now(UTC),
                payload={
                    "code": "upstream_transient",
                    "message": "模型服务连接不稳定",
                    "recoverable": True,
                },
            ),
        ]
        for event in events:
            runtime.persistent_store.append_event(sid, event)

        with client.stream(
            "POST",
            f"/v1/sessions/{sid}/continue-tool-result",
            json={"runId": "continue-client-run"},
            headers={"accept": "text/event-stream"},
        ) as resp:
            body = "".join(resp.iter_text())

        assert resp.status_code == 200
        assert "new answer" in body
        assert "continue-client-run" in body
        assert client.get(f"/v1/sessions/{sid}?view=active").json()["recovery"] is None
        assert _RouteLLM.seen_tools[-1]
        assert any(
            message.get("role") == "tool" and message.get("content") == "saved result"
            for message in _RouteLLM.seen_messages[-1]
        )
        assert any(
            '<surface type="desktop_chat" />' in message.get("content", "")
            for message in _RouteLLM.seen_messages[-1]
        )


def test_continue_route_forwards_compaction_mode_after_source_validation(
    client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent import TurnDone

    sid = client.post("/v1/sessions", json={}).json()["session_id"]
    for event in (
        Event(
            type="user_message",
            uuid="compact-goal",
            ts=datetime.now(UTC),
            payload={"content": "keep this task"},
        ),
        Event(
            type="run_error",
            uuid="compact-failure",
            ts=datetime.now(UTC),
            payload={
                "code": "context_compaction_unavailable",
                "recoverable": True,
                "run_id": "old",
                "message": "fixture",
            },
        ),
    ):
        runtime.persistent_store.append_event(sid, event)
    seen: list[dict[str, Any]] = []

    def capture(self: Conversation, **kwargs: Any) -> Iterator[TurnDone]:
        seen.append(kwargs)
        yield TurnDone(stop_reason="end_turn")

    monkeypatch.setattr(Conversation, "continue_after_tool_error", capture)
    invalid = client.post(
        f"/v1/sessions/{sid}/continue-tool-result", json={"recoveryMode": "unsafe"}
    )
    assert invalid.status_code == 422 and not seen
    response = client.post(
        f"/v1/sessions/{sid}/continue-tool-result",
        json={
            "runId": "compact-retry",
            "sourceRunId": "old",
            "recoveryMode": "compact_then_resume",
        },
    )
    assert response.status_code == 200
    assert seen[0]["recovery_mode"] == "compact_then_resume"
    assert seen[0]["source_run_id"] == "old"
    saved = runtime.persistent_store.load(sid)
    assert sum(event.type == "user_message" for event in saved.active_events) == 1


# ===== POST /ag-ui/run channel boundary =====


class TestAgUiRunChannelBoundary:
    def test_text_run_uses_text_prompt_without_mutating_voice_session(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={"channel": "voice"}).json()["session_id"]
        assert runtime.persistent_session_manager.open(sid).current_channel == "voice"

        resp = client.post(
            "/ag-ui/run",
            json={
                "thread_id": sid,
                "run_id": "run-text-1",
                "messages": [{"id": "u-1", "role": "user", "content": "这是啥情况"}],
                "tools": [],
                "context": [],
                "forwarded_props": {},
                "state": {},
            },
            headers={"accept": "text/event-stream"},
        )

        assert resp.status_code == 200
        assert "new answer" in resp.text
        loaded = runtime.persistent_session_manager.open(sid)
        assert loaded.current_channel == "voice"
        assert _RouteLLM.seen_messages
        system_prompt = _RouteLLM.seen_messages[-1][0]["content"]
        assert "正在通过电话和用户对话" not in system_prompt
        messages = _RouteLLM.seen_messages[-1]
        context_index = next(
            index
            for index, message in enumerate(messages)
            if '<surface type="desktop_chat" />' in message.get("content", "")
        )
        assert messages[context_index]["role"] == "user"
        user_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("content") == "这是啥情况"
        )
        assert context_index < user_index

    def test_text_run_error_persists_run_error_event(
        self, client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        def _fail_stream(
            self: _RouteLLM,
            messages: list[dict[str, Any]],
            *,
            tools: list[dict[str, Any]] | None = None,
            **overrides: Any,
        ) -> Iterator[LLMStreamEvent]:
            raise RuntimeError("upstream overloaded")
            yield  # pragma: no cover

        monkeypatch.setattr(_RouteLLM, "stream", _fail_stream)

        resp = client.post(
            "/ag-ui/run",
            json={
                "thread_id": sid,
                "run_id": "run-error-1",
                "messages": [{"id": "u-1", "role": "user", "content": "继续说"}],
                "tools": [],
                "context": [],
                "forwarded_props": {},
                "state": {},
            },
            headers={"accept": "text/event-stream"},
        )

        assert resp.status_code == 200
        assert "RUN_ERROR" in resp.text
        assert "这轮回答中断了，没有生成完整回复。请稍后重试。" in resp.text
        loaded = runtime.persistent_session_manager.open(sid)
        assert [
            event.type
            for event in loaded.events
            if event.type
            not in {
                "rollout_segment",
                "model_transcript_item",
                "rollout_request_prepared",
                "rollout_commit",
                "rollout_value",
            }
        ] == [
            "session_meta",
            "user_message",
            "assistant_phase",
            "run_error",
        ]
        user_events = [event for event in loaded.events if event.type == "user_message"]
        assert len(user_events) == 1
        assert user_events[0].payload == {"content": "继续说"}
        run_errors = [event for event in loaded.events if event.type == "run_error"]
        assert len(run_errors) == 1
        assert run_errors[0].payload == {
            "code": "internal_error",
            "message": "这轮回答中断了，没有生成完整回复。请稍后重试。",
            "recoverable": False,
            "source": "ag_ui",
            "run_id": "run-error-1",
        }

    def test_text_context_build_error_keeps_user_before_run_error(
        self, client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        def _fail_context_build(
            self: Conversation, *args: Any, **kwargs: Any
        ) -> Generator[Any, None, list[dict[str, Any]]]:
            del self, args, kwargs
            raise RuntimeError("attachment provider render failed")
            yield  # pragma: no cover

        monkeypatch.setattr(
            Conversation,
            "_build_openai_messages_first_turn_stream",
            _fail_context_build,
        )

        resp = client.post(
            "/ag-ui/run",
            json={
                "thread_id": sid,
                "run_id": "run-context-error",
                "messages": [
                    {
                        "id": "user-context-error",
                        "role": "user",
                        "content": "这条 query 不能消失",
                    }
                ],
                "tools": [],
                "context": [],
                "forwarded_props": {},
                "state": {},
            },
            headers={"accept": "text/event-stream"},
        )

        assert resp.status_code == 200
        assert "RUN_ERROR" in resp.text
        loaded = runtime.persistent_session_manager.open(sid)
        assert [event.type for event in loaded.active_events] == [
            "session_meta",
            "user_message",
            "run_error",
        ]
        assert loaded.events[1].uuid == "user-context-error"
        assert loaded.events[1].payload == {"content": "这条 query 不能消失"}
        assert loaded.active_events[2].payload["run_id"] == "run-context-error"


# ===== POST /v1/sessions/{id}/model =====


class TestSwitchModelEndpoint:
    def test_switch_does_not_open_or_restore_conversation(
        self, client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        manager = runtime.persistent_session_manager

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("configuration update must not cold-restore a conversation")

        monkeypatch.setattr(manager, "open", forbidden)
        monkeypatch.setattr(manager, "start_conversation", forbidden)
        response = client.post(f"/v1/sessions/{sid}/model", json={"model": "kimi/kimi-k2.6"})
        assert response.status_code == 200
        assert manager.query(sid).current_model == "kimi/kimi-k2.6"
        assert manager.runtime_state_diagnostics(sid) is None

    def test_switch_model_persists_event(self, client: TestClient, runtime: BridgeRuntime) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        response = client.post(
            f"/v1/sessions/{sid}/model",
            json={"model": "kimi/kimi-k2.6"},
        )

        assert response.status_code == 200
        assert response.json() == {"session_id": sid, "model": "kimi/kimi-k2.6"}
        loaded = runtime.persistent_session_manager.open(sid)
        assert loaded.current_model == "kimi/kimi-k2.6"
        changes = [event for event in loaded.events if event.type == "model_config_change"]
        assert len(changes) == 1
        assert changes[0].payload["to"] == {
            "model": "kimi/kimi-k2.6",
            "reasoningPreset": "on",
        }

    def test_switch_model_is_idempotent(self, client: TestClient, runtime: BridgeRuntime) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        client.post(f"/v1/sessions/{sid}/model", json={"model": "kimi/kimi-k2.6"})
        before = len(runtime.persistent_session_manager.open(sid).events)

        response = client.post(
            f"/v1/sessions/{sid}/model",
            json={"model": "kimi/kimi-k2.6"},
        )

        assert response.status_code == 200
        assert len(runtime.persistent_session_manager.open(sid).events) == before

    def test_switch_model_returns_stable_errors(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        unsupported = client.post(
            f"/v1/sessions/{sid}/model",
            json={"model": "unknown/model"},
        )
        assert unsupported.status_code == 400
        assert unsupported.json()["error"]["code"] == "unsupported_model"

        missing = client.post(
            "/v1/sessions/missing/model",
            json={"model": "kimi/kimi-k2.6"},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "session_not_found"

        with runtime.turn_coordinator.hold(sid):
            busy = client.post(
                f"/v1/sessions/{sid}/model",
                json={"model": "kimi/kimi-k2.6"},
            )
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "session_busy"


class TestRuntimeConfigEndpoint:
    def test_create_and_partial_update_persist_atomic_config(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        created = client.post(
            "/v1/sessions",
            json={"model": "kimi/kimi-k2.6", "reasoningPreset": "off"},
        )
        assert created.status_code == 200
        sid = created.json()["session_id"]
        assert created.json()["reasoningPreset"] == "off"

        response = client.patch(
            f"/v1/sessions/{sid}/runtime-config",
            json={"reasoningPreset": "on"},
        )

        assert response.status_code == 200
        assert response.json()["runtimeConfig"] == {
            "model": "kimi/kimi-k2.6",
            "reasoningPreset": "on",
        }
        loaded = runtime.persistent_session_manager.open(sid)
        assert loaded.current_model_config == ModelRuntimeConfig("kimi/kimi-k2.6", "on")
        assert loaded.events[-1].type == "model_config_change"
        summary = next(
            item for item in client.get("/v1/sessions").json() if item["session_id"] == sid
        )
        assert summary["model"] == "kimi/kimi-k2.6"
        assert summary["reasoningPreset"] == "on"
        assert "reasoning_preset" not in summary

    def test_model_only_update_uses_target_default(self, client: TestClient) -> None:
        sid = client.post(
            "/v1/sessions",
            json={"model": "kimi/kimi-k2.6", "reasoningPreset": "off"},
        ).json()["session_id"]

        response = client.patch(
            f"/v1/sessions/{sid}/runtime-config",
            json={"model": "openrouter/openai/gpt-5.6-luna"},
        )

        assert response.status_code == 200
        assert response.json()["runtimeConfig"] == {
            "model": "openrouter/openai/gpt-5.6-luna",
            "reasoningPreset": "medium",
        }

    def test_invalid_preset_and_busy_use_stable_errors(
        self, client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BridgeRuntime, "session_files_available", property(lambda _: True))
        sid = client.post(
            "/v1/sessions",
            json={"model": "kimi/kimi-k2.6"},
        ).json()["session_id"]

        unsupported = client.patch(
            f"/v1/sessions/{sid}/runtime-config",
            json={"reasoningPreset": "max"},
        )
        assert unsupported.status_code == 400
        assert unsupported.json()["error"]["code"] == "unsupported_reasoning_preset"

        with runtime.turn_coordinator.hold(sid):
            busy = client.patch(
                f"/v1/sessions/{sid}/runtime-config",
                json={"reasoningPreset": "off"},
            )
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "session_busy"


# ===== POST /v1/sessions/{id}/channel =====


class TestSwitchChannelEndpoint:
    def test_switch_text_to_voice(self, client: TestClient, runtime: BridgeRuntime) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        resp = client.post(f"/v1/sessions/{sid}/channel", json={"channel": "voice"})
        assert resp.status_code == 200
        assert resp.json()["channel"] == "voice"

        # 验证落盘了 channel_change 事件
        loaded = runtime.persistent_session_manager.open(sid)
        change_events = [e for e in loaded.events if e.type == "channel_change"]
        assert len(change_events) == 1
        assert change_events[0].payload["to"] == "voice"
        assert change_events[0].payload["from"] == "text"

    def test_switch_idempotent_no_event(self, client: TestClient, runtime: BridgeRuntime) -> None:
        """切换到当前 channel 不应再写事件。"""
        sid = client.post("/v1/sessions", json={"channel": "voice"}).json()["session_id"]
        before = runtime.persistent_session_manager.open(sid)
        before_count = len(before.events)
        resp = client.post(f"/v1/sessions/{sid}/channel", json={"channel": "voice"})
        assert resp.status_code == 200
        after = runtime.persistent_session_manager.open(sid)
        assert len(after.events) == before_count

    def test_switch_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/sessions/nonexistent-uuid/channel",
            json={"channel": "voice"},
        )
        assert resp.status_code == 404

    def test_invalid_channel_value_returns_422(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        resp = client.post(f"/v1/sessions/{sid}/channel", json={"channel": "garbage"})
        # pydantic Literal 校验失败 → 422
        assert resp.status_code == 422

    def test_switch_back_appends_another_event(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        client.post(f"/v1/sessions/{sid}/channel", json={"channel": "voice"})
        client.post(f"/v1/sessions/{sid}/channel", json={"channel": "text"})
        loaded = runtime.persistent_session_manager.open(sid)
        change_events = [e for e in loaded.events if e.type == "channel_change"]
        assert len(change_events) == 2
        assert change_events[0].payload["to"] == "voice"
        assert change_events[1].payload["to"] == "text"
        assert loaded.current_channel == "text"


def test_destructive_session_mutations_reject_busy_session(
    client: TestClient,
    runtime: BridgeRuntime,
) -> None:
    sid = client.post("/v1/sessions", json={}).json()["session_id"]

    with runtime.turn_coordinator.hold(sid):
        responses = [
            client.patch(f"/v1/sessions/{sid}", json={"archived": True}),
            client.delete(f"/v1/sessions/{sid}"),
            client.post(f"/v1/sessions/{sid}/persona", json={"persona": "default"}),
            client.post(f"/v1/sessions/{sid}/channel", json={"channel": "voice"}),
        ]

    assert [response.status_code for response in responses] == [409, 409, 409, 409]
    assert all(response.json()["error"]["code"] == "session_busy" for response in responses)
    assert runtime.persistent_session_manager.open(sid).current_channel == "text"


@pytest.mark.parametrize(
    "old_model",
    [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-flash-vision-exp",
    ],
)
def test_removed_model_history_run_error_and_manual_switch_recovery(
    client: TestClient,
    runtime: BridgeRuntime,
    old_model: str,
) -> None:
    from llm_providers import build_provider_spec

    def factory(config: ModelRuntimeConfig) -> LanguageModel:
        # Real route admission, with a fake generator: no external requests.
        build_provider_spec(provider_id="deepseek", api_key="fixture", runtime_config=config)
        return _RouteLLM()

    runtime.persistent_session_manager._llm_client_factory = factory
    from dataclasses import replace

    runtime = replace(runtime, llm_client_factory=factory, default_model=old_model)
    client = TestClient(create_app_with_runtime(runtime))
    created = client.post("/v1/sessions", json={})
    assert created.status_code == 200
    sid = created.json()["session_id"]
    assert created.json()["model"] == old_model
    detail = client.get(f"/v1/sessions/{sid}")
    assert detail.status_code == 200
    assert detail.json()["model"] == old_model

    def run(run_id: str) -> Any:
        return client.post(
            "/ag-ui/run",
            json={
                "thread_id": sid,
                "run_id": run_id,
                "messages": [{"id": run_id, "role": "user", "content": "hello"}],
                "tools": [],
                "context": [],
                "forwarded_props": {},
                "state": {},
            },
            headers={"accept": "text/event-stream"},
        )

    failed = run("removed-model-run")
    assert "model_route_unavailable" in failed.text
    assert not _RouteLLM.seen_messages
    assert runtime.persistent_session_manager.open(sid).current_model == old_model
    switched = client.patch(
        f"/v1/sessions/{sid}/runtime-config",
        json={
            "model": "deepseek/deepseek-flash",
            "reasoningPreset": "off",
        },
    )
    assert switched.status_code == 200
    assert "new answer" in run("new-model-run").text
    assert runtime.persistent_session_manager.open(sid).initial_model == old_model
