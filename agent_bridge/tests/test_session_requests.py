from __future__ import annotations

import dataclasses
import os
import threading
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent.approvals import ApprovalBroker
from agent.runtime import AgentRuntime, RunHandle, RunOutcome
from agent.scheduled_tasks import ScheduledTaskService, ScheduledTaskStore
from agent.scheduling import OnceSchedule
from agent.session_requests import RequestOptions, SessionRequestStore
from agent.sessions.metadata import SessionMetadataStore
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.attachment_reconciliation import reconcile_attachments
from agent_bridge.session_bridge import PersistentBootstrap
from agent_bridge.session_requests import SessionRequestService
from agent_bridge.settings import BridgeSettings

from agent import (
    AttachmentStore,
    Conversation,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    Session,
    SessionManager,
    make_default_registry,
)
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ModelRuntimeConfig,
)


@pytest.mark.asyncio
async def test_startup_attachment_gc_preserves_old_journal_reference_without_index(
    tmp_path: Path,
) -> None:
    assets = AttachmentStore(tmp_path / "assets")

    async def chunks(content: bytes) -> AsyncIterator[bytes]:
        yield content

    refs = []
    for content in (b"referenced", b"orphan"):
        draft, ref = await assets.import_stream(chunks(content), display_name="document.txt")
        assets.consume_draft(draft)
        assets.release_draft(draft)
        os.utime(assets.metadata_dir / f"{ref.asset_id}.json", (1, 1))
        refs.append(ref)
    root = tmp_path / "sessions"
    store = JsonlSessionStore(root)
    session = Session.new("old", "default", "test/model", session_id="legacy")
    session.events.append(
        Event(
            "user_message",
            "u",
            datetime.now(UTC),
            {"content": "hi", "attachments": [refs[0].to_dict()]},
        )
    )
    store._path("legacy").write_text(
        "\n".join(e.to_jsonl() for e in session.events) + "\n", encoding="utf-8"
    )
    manager = SessionManager(store)
    try:
        runtime = cast(
            BridgeRuntime,
            SimpleNamespace(
                attachment_store=assets,
                persistent_session_manager=manager,
                persistent_store=store,
                scheduled_task_store=None,
                session_request_service=None,
            ),
        )
        result = reconcile_attachments(runtime, threading.Event())
        assert result and result["removed_assets"] == 1
        assert assets.verify(refs[0].asset_id).read_bytes() == b"referenced"
        assert not store._business_index.path("legacy").exists()
        assert not store._configuration.path("legacy").exists()
    finally:
        manager.close()


def test_prepared_admission_avoids_rebuilding_business_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("prepared", "default", "deepseek/deepseek-chat")
    store.create(session)
    manager = SessionManager(store)
    service = object.__new__(SessionRequestService)
    service.runtime = cast(BridgeRuntime, SimpleNamespace(persistent_session_manager=manager))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("full business query")

    try:
        manager.prepare_execution(session.session_id)
        deadline = time.monotonic() + 3
        while manager.preparation_status(session.session_id) == "preparing":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        monkeypatch.setattr(manager, "query", forbidden)
        service.validate_target(session.session_id)
        manager.cancel_preparation(session.session_id)
        with pytest.raises(AssertionError, match="full business query"):
            service.validate_target(session.session_id)
    finally:
        manager.close()


def test_common_service_serializes_requests_and_uses_isolated_overrides(tmp_path: Path) -> None:
    started, release = threading.Event(), threading.Event()
    seen = []

    class FakeLLM:
        context_window = 128000

        def __init__(self, model: ModelRuntimeConfig) -> None:
            self.model = model

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            seen.append((self.model.model_id, request.messages))
            if len(seen) == 1:
                started.set()
                assert release.wait(5)
            yield LLMTextDelta("ok")
            yield LLMTurnDone(stop_reason="end_turn")

        def generate(self, request: ModelRequest) -> ModelResponse:
            raise AssertionError("Unexpected non-streaming call")

    settings = BridgeSettings(sessions_dir=tmp_path / "sessions", memory_enabled=False)
    store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tools = make_default_registry()

    def prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    manager = SessionManager(
        store=store,
        llm_client_factory=FakeLLM,
        prompt_builder_factory=prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tools,
        metadata_store=metadata,
    )
    session = manager.create(persona="default", model="deepseek/deepseek-chat")
    agent_runtime = AgentRuntime(conversation_factory=lambda sid: cast(Conversation, None))
    broker = ApprovalBroker(mode_store=metadata)
    runtime = BridgeRuntime(
        settings=settings,
        persistent_store=store,
        transient_store=store,
        persistent_session_manager=manager,
        catalog=catalog,
        tool_registry=tools,
        prompt_builder_factory=prompt_factory,
        default_persona="default",
        default_model="deepseek/deepseek-chat",
        agent_runtime=agent_runtime,
        approval_broker=broker,
    )
    requests = SessionRequestService(runtime)
    runtime = dataclasses.replace(runtime, session_request_service=requests)

    def submit(run_id: str, **kwargs: Any) -> RunHandle:
        return requests.submit(
            boot=PersistentBootstrap(
                thread_id=session.session_id,
                new_user_input=run_id,
                default_persona="default",
                default_model="deepseek/deepseek-chat",
            ),
            run_id=run_id,
            user_event_id=f"user-{run_id}",
            **kwargs,
        )

    agent_runtime.start()
    try:
        first = submit("manual")
        assert started.wait(3)
        source = {
            "kind": "scheduled_task",
            "task_id": "t",
            "task_name": "daily",
            "occurrence_id": "o",
        }
        second = submit(
            "scheduled",
            source=source,
            options=RequestOptions(
                model=ModelRuntimeConfig("deepseek/deepseek-reasoner", "none"),
                approval_mode="session_auto",
            ),
        )
        cancelled = submit("cancelled")
        requests.cancel(session.session_id, "cancelled")
        terminal = cancelled.wait(2)
        assert terminal is not None and terminal.outcome == RunOutcome.CANCELLED
        receipt = requests.store.get("scheduled")
        assert receipt is not None and receipt["status"] == "queued"
        assert not any(e.uuid == "user-scheduled" for e in manager.query(session.session_id).events)
        release.set()
        terminal = first.wait(5)
        assert terminal is not None and terminal.outcome == RunOutcome.COMPLETED
        terminal = second.wait(5)
        assert terminal is not None and terminal.outcome == RunOutcome.COMPLETED
        final = submit("manual-again")
        terminal = final.wait(5)
        assert terminal is not None and terminal.outcome == RunOutcome.COMPLETED
        events = manager.query(session.session_id).events
        users = [e for e in events if e.type == "user_message"]
        assert [e.uuid for e in users] == ["user-manual", "user-scheduled", "user-manual-again"]
        assert users[1].payload["request_source"] == source
        assert "request_source" not in users[2].payload
        assert [model for model, _ in seen] == [
            "deepseek/deepseek-chat",
            "deepseek/deepseek-reasoner",
            "deepseek/deepseek-chat",
        ]
        assert manager.query(session.session_id).current_model == "deepseek/deepseek-chat"
        assert broker.mode_state(session.session_id).mode == "per_request"
    finally:
        release.set()
        agent_runtime.stop()


@pytest.mark.asyncio
async def test_task_and_occurrence_assets_survive_draft_expiry_and_task_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets = AttachmentStore(tmp_path / "assets")

    async def chunks() -> AsyncIterator[bytes]:
        yield b"persistent request document"

    draft_id, ref = await assets.import_stream(chunks(), display_name="brief.txt")
    assets.consume_draft(draft_id)
    assets.release_draft(draft_id)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    tasks = ScheduledTaskStore(tmp_path / "tasks.db")
    service = ScheduledTaskService(tasks, clock=lambda: now)
    task = service.create(
        name="files",
        instruction="",
        target_session_id="s",
        request_options=RequestOptions(attachments=(ref,)),
        schedule=OnceSchedule(datetime(2027, 1, 1), "UTC"),
        persona_name="default",
        model_id="fake",
    )
    requests = SessionRequestStore(tmp_path / "requests.db")
    runtime = cast(
        BridgeRuntime,
        SimpleNamespace(
            attachment_store=assets,
            persistent_session_manager=SimpleNamespace(list=lambda **kwargs: []),
            scheduled_task_store=tasks,
            session_request_service=SimpleNamespace(store=requests),
        ),
    )
    original = assets.reconcile

    def sweep(ids: set[str]) -> dict[str, int]:
        return original(ids, grace_seconds=0, now=time.time() + 3600)

    monkeypatch.setattr(assets, "reconcile", sweep)
    assert reconcile_attachments(runtime) is not None
    assert assets.verify(ref.asset_id).read_bytes() == b"persistent request document"
    occurrence = service.run_now(task.id)
    service.delete(task.id, revision=task.revision)
    assert reconcile_attachments(runtime) is not None
    assert assets.verify(ref.asset_id).read_bytes() == b"persistent request document"
    assert tasks.get_occurrence(occurrence.id).task_snapshot.request_options.attachments == (ref,)
