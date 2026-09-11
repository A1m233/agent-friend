from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent.notifications import NotificationConflict, NotificationFact, NotificationStore
from agent.personas import EffectivePersonaStore
from agent.runtime.models import RunOutcome, RunSnapshot, RunStatus
from agent.sessions.metadata import SessionMetadataStore
from agent.todos import (
    AgentResponseStatus,
    OccurrenceState,
    ReminderFacts,
    ReminderOccurrence,
    TodoStatus,
)
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.notifications.generation import NotificationGenerator, memory_block
from agent_bridge.notifications.service import NotificationService
from agent_bridge.presence import DesktopPresenceSnapshot, PresenceRegistry
from agent_bridge.routes.notifications import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from memory.contracts import MemoryContext, MemoryItem

from agent import Event, JsonlSessionStore, Session
from llm_providers import LLMTextDelta, LLMTurnDone, ModelRequest, minimum_model_runtime_config
from runtime_config import DesktopSettingsReader

NOW = datetime(2026, 9, 6, tzinfo=UTC)


class FakeGenerator:
    calls = 0

    def generate(self, fact: NotificationFact, **kwargs: Any) -> str:
        self.calls += 1
        return "照片整理有新回复了，回来看看吧。"


def service(
    tmp_path: Path, *, session_metadata_store: SessionMetadataStore | None = None
) -> tuple[NotificationService, FakeGenerator]:
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.create(Session.new(title="整理照片", persona="default", model="fake", session_id="s"))
    reader = DesktopSettingsReader(tmp_path / "settings.json")
    reader.path.write_text("{}", encoding="utf-8")
    generator = FakeGenerator()
    result = NotificationService(
        store=NotificationStore(tmp_path / "notifications.db"),
        sessions=sessions,
        presence=PresenceRegistry(),
        reader=reader,
        generator=cast(NotificationGenerator, generator),
        publisher=lambda _: None,
        session_metadata_store=session_metadata_store,
        clock=lambda: NOW,
    )
    return result, generator


def fact() -> NotificationFact:
    return NotificationFact(
        event_key="run:s:r",
        kind="reply_ready",
        title="整理照片",
        persona_id="p",
        session_id="s",
        run_id="r",
        created_at=NOW,
    )


def test_snapshot_existence_check_does_not_load_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, _ = service(tmp_path)
    item = svc.accept(fact())

    def unexpected_load(*args: object, **kwargs: object) -> None:
        raise AssertionError("existence validation must not parse conversation history")

    monkeypatch.setattr(svc.sessions, "load", unexpected_load)
    assert svc.snapshot()[0]["delivery"]["status"] != "invalidated"
    svc.sessions.delete("s")
    assert svc.snapshot()[0]["delivery"]["status"] == "invalidated"
    assert svc.store.get(item.notification_id).generation_status == "suppressed"


def desktop(current: str = "s", state: str = "active") -> DesktopPresenceSnapshot:
    return DesktopPresenceSnapshot(NOW, current, True, True, False, 0, cast(Any, state), NOW)


def test_viewing_suppresses_before_generation_and_snapshot_has_tombstone(tmp_path: Path) -> None:
    svc, generator = service(tmp_path)
    svc.presence.update_desktop(desktop())
    item = svc.accept(fact())
    assert item.generation_status == "suppressed"
    assert not svc.run_once()
    assert generator.calls == 0
    assert svc.snapshot()[0]["delivery"]["status"] == "invalidated"


def test_locked_desktop_defers_and_resolved_source_cannot_present(tmp_path: Path) -> None:
    svc, generator = service(tmp_path)
    item = svc.accept(fact())
    svc.presence.update_desktop(desktop(state="locked"))
    assert not svc.run_once()
    assert generator.calls == 0
    svc.presence.update_desktop(replace(desktop("other"), observed_at=NOW + timedelta(seconds=1)))
    assert svc.run_once()
    ready = svc.store.get(item.notification_id)
    svc.presence.update_desktop(replace(desktop(), observed_at=NOW + timedelta(seconds=2)))
    with pytest.raises(NotificationConflict):
        svc.prepare(item.notification_id, version=ready.version, window_id="bubble")


def test_routes_receipt_is_idempotent_and_memory_is_not_generation(tmp_path: Path) -> None:
    svc, generator = service(tmp_path)
    episodes: list[Any] = []

    def record(ep: Any) -> str:
        episodes.append(ep)
        return "inserted"

    svc.memory = cast(Any, SimpleNamespace(record_host_episode=record))
    item = svc.accept(fact())
    assert svc.run_once()
    assert episodes == []
    app = FastAPI()
    register_routes(
        app,
        cast(
            BridgeRuntime,
            SimpleNamespace(notification_service=svc, settings=SimpleNamespace(dev_mode=False)),
        ),
    )
    client = TestClient(app)
    ready = client.get("/v1/notifications").json()["notifications"][0]
    url = f"/v1/notifications/{item.notification_id}"
    prepared = client.post(
        url + "/prepare", json={"version": ready["version"], "windowId": "bubble"}
    ).json()
    payload = {"attempt": prepared["attempt"], "text": prepared["text"], "truncated": False}
    assert client.post(url + "/presented", json=payload).json()["playSound"]
    assert not client.post(url + "/presented", json=payload).json()["playSound"]
    svc.run_once()
    svc.run_once()
    assert len(episodes) == 1
    assert episodes[0].notification_id == item.notification_id
    assert generator.calls == 1


def test_terminal_and_recovery_share_id_and_todo_agent_is_excluded(tmp_path: Path) -> None:
    svc, _ = service(tmp_path)
    svc.sessions.append_event(
        "s",
        Event(
            type="assistant_message",
            uuid="a",
            ts=NOW,
            payload={"content": "你希望整理哪个目录？"},
            meta={"run_id": "r"},
        ),
    )
    snapshot = RunSnapshot(
        run_id="r",
        session_id="s",
        status=RunStatus.TERMINAL,
        outcome=RunOutcome.COMPLETED,
        reason_code="end_turn",
        channel="text",
        origin="ag_ui",
        control_scope=None,
        created_at=NOW,
        started_at=NOW,
        finished_at=NOW,
        version=3,
    )
    svc.on_terminal(replace(snapshot, source_kind="todo_reminder"))
    assert svc.snapshot() == []
    svc.on_terminal(snapshot)
    assert svc.store.list()[0].fact.reply == "你希望整理哪个目录？"
    svc.on_recovered(
        svc.sessions.load("s"),
        Event(type="run_terminal", uuid="new", ts=NOW, payload={"run_id": "r"}),
    )
    assert len(svc.snapshot()) == 1


@pytest.mark.parametrize(
    "outcome,reason,presentation,expected_kind",
    [
        (RunOutcome.COMPLETED, "end_turn", "conversation", "reply_ready"),
        (RunOutcome.FAILED, "executor_error", "conversation", "run_failed"),
        (RunOutcome.CANCELLED, "client_disconnected", "conversation", "run_interrupted"),
        (RunOutcome.COMPLETED, "end_turn", "ambient", "ambient"),
    ],
)
def test_terminal_notifications_use_the_current_session_display_title(
    tmp_path: Path, outcome: RunOutcome, reason: str, presentation: str, expected_kind: str
) -> None:
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    metadata.set_display_title("s", "旅行照片方案")
    svc, generator = service(tmp_path, session_metadata_store=metadata)
    svc.sessions.append_event(
        "s",
        Event(
            type="assistant_message",
            uuid="reply",
            ts=NOW,
            payload={"content": "你希望按年份还是地点分类？"},
            meta={"run_id": "r"},
        ),
    )
    svc.on_terminal(
        RunSnapshot(
            run_id="r",
            session_id="s",
            status=RunStatus.TERMINAL,
            outcome=outcome,
            reason_code=reason,
            channel="text",
            origin="ag_ui",
            control_scope=None,
            created_at=NOW,
            started_at=NOW,
            finished_at=NOW,
            version=3,
            presentation=presentation,
        )
    )
    item = svc.store.list()[0]
    assert item.fact.title == "旅行照片方案"
    assert item.fact.kind == expected_kind
    assert svc.sessions.load("s").initial_title == "整理照片"
    assert generator.calls == 0


def test_session_rename_updates_later_approval_and_recovery_notification_titles(
    tmp_path: Path,
) -> None:
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    metadata.set_display_title("s", "旅行照片方案")
    svc, generator = service(tmp_path, session_metadata_store=metadata)
    svc.approvals = cast(
        Any, SimpleNamespace(pending=lambda: [SimpleNamespace(request_id="approval-title")])
    )
    svc.on_approval(
        {"request_id": "approval-title", "session_id": "s", "run_id": "r", "status": "pending"}
    )
    first = svc.store.list()[0]
    assert first.fact.kind == "needs_action"
    assert first.fact.title == "旅行照片方案"
    metadata.set_display_title("s", "家庭相册整理")
    svc.on_recovered(
        svc.sessions.load("s"),
        Event(type="run_terminal", uuid="recovery", ts=NOW, payload={"run_id": "later-run"}),
    )
    recovered = next(item for item in svc.store.list() if item.fact.kind == "run_interrupted")
    assert recovered.fact.title == "家庭相册整理"
    assert svc.store.get(first.notification_id).fact.title == "旅行照片方案"
    assert generator.calls == 0


@pytest.mark.parametrize(
    "metadata_state",
    ["not-injected", "missing-file", "empty-title", "invalid-json", "invalid-utf8", "io-error"],
)
def test_unavailable_session_display_title_falls_back_without_losing_notification(
    tmp_path: Path, metadata_state: str
) -> None:
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    if metadata_state == "empty-title":
        metadata.set_display_title("s", "")
    elif metadata_state == "invalid-json":
        metadata.path.write_text("{invalid", encoding="utf-8")
    elif metadata_state == "invalid-utf8":
        metadata.path.write_bytes(b"\xff")
    elif metadata_state == "io-error":
        metadata.path.mkdir()
    svc, generator = service(
        tmp_path, session_metadata_store=None if metadata_state == "not-injected" else metadata
    )
    svc.on_recovered(
        svc.sessions.load("s"),
        Event(type="run_terminal", uuid="recovery", ts=NOW, payload={"run_id": "r"}),
    )
    item = svc.store.list()[0]
    assert item.fact.title == "整理照片"
    assert item.generation_status == "pending"
    assert item.delivery["status"] == "pending"
    assert generator.calls == 0


def test_todo_notification_keeps_its_todo_title_when_session_has_a_display_title(
    tmp_path: Path,
) -> None:
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    metadata.set_display_title("s", "旅行照片方案")
    svc, generator = service(tmp_path, session_metadata_store=metadata)
    occurrence = ReminderOccurrence(
        id="occurrence-title",
        reminder_id="reminder-title",
        todo_id="todo-title",
        scheduled_at_utc=NOW,
        facts=ReminderFacts(
            occurrence_id="occurrence-title",
            todo_id="todo-title",
            reminder_id="reminder-title",
            title="起身活动",
            description="喝点水。",
            scheduled_at_utc=NOW,
            scheduled_local=NOW,
            timezone="UTC",
            recurrence="once",
            todo_status=TodoStatus.OPEN,
        ),
        state=OccurrenceState.DELIVERED,
        destination_kind="conversation",
        destination_id="s",
        delivery_receipt=None,
        agent_response_status=AgentResponseStatus.PENDING,
        attempt_count=0,
        next_attempt_at=None,
        claim_until=None,
        created_at=NOW,
        updated_at=NOW,
        persistence_session_id="s",
    )
    notification_id = svc.on_todo(occurrence)
    item = svc.store.get(notification_id)
    assert item.fact.kind == "todo_due"
    assert item.fact.title == "起身活动"
    assert generator.calls == 0


@pytest.mark.parametrize("phased", [False, True])
@pytest.mark.parametrize(
    "provider,model,preset,budget",
    [
        ("kimi", "kimi/kimi-k3", "low", 2048),
        ("deepseek", "deepseek/deepseek-flash", "off", 512),
        (
            "custom",
            "custom/00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002",
            "none",
            2048,
        ),
    ],
)
def test_prompt_uses_settings_minimum_persona_and_filtered_memory(
    tmp_path: Path, phased: bool, provider: str, model: str, preset: str, budget: int
) -> None:
    reader = DesktopSettingsReader(tmp_path / "settings.json")
    reader.path.write_text(
        json.dumps({"chatModel": {"provider": provider, "model": model}}), encoding="utf-8"
    )
    persona = EffectivePersonaStore(
        user_file=tmp_path / "persona.md",
        internal_dir=tmp_path / "internal",
        builtin_loader=lambda: "# 同伴\n自然交流。",
    )
    context = MemoryContext(
        "must-not-inject-rendered",
        [
            MemoryItem("用户喜欢短句", "core", "core", 1),
            MemoryItem("旧气泡", "episodic", "episodic:notification-presented:old", 1),
            MemoryItem("用户整理照片", "episodic", "episodic:normal", 1),
        ],
    )
    requests: list[ModelRequest] = []
    configs: list[Any] = []

    def stream(request: ModelRequest) -> Iterator[LLMTextDelta | LLMTurnDone]:
        from llm_providers import ProviderAssistantItem, ProviderAssistantTurn

        requests.append(request)
        text = "照片有新回复，回来看看吧。"
        if phased:
            yield LLMTextDelta("过程说明" * 100, phase="commentary")
            yield LLMTextDelta(text, phase="final_answer")
            yield LLMTurnDone(
                "end_turn",
                assistant_turn=ProviderAssistantTurn(
                    "过程说明" * 100 + text,
                    text_items=(
                        ProviderAssistantItem("progress", 0, "过程说明" * 100, "commentary"),
                        ProviderAssistantItem("final", 1, text, "final_answer"),
                    ),
                ),
            )
        else:
            yield LLMTextDelta(text)
            yield LLMTurnDone("end_turn")

    def factory(config: Any) -> Any:
        configs.append(config)
        return SimpleNamespace(stream=stream)

    memory = SimpleNamespace(retrieve=lambda *args, **kwargs: context)
    gen = NotificationGenerator(
        reader=reader, factory=cast(Any, factory), persona=persona, memory=cast(Any, memory)
    )
    assert (
        gen.generate(fact(), stopping=threading.Event(), still_valid=lambda: True)
        == "照片有新回复，回来看看吧。"
    )
    assert configs[0].reasoning_preset == preset
    assert requests[0].settings["max_tokens"] == budget
    assert requests[0].tools is None
    assert not requests[0].control.allow_retries
    text = str(requests[0].messages)
    assert "自然交流" in text and "用户喜欢短句" in text
    assert "旧气泡" not in text and "must-not-inject-rendered" not in text
    assert memory_block(context)[1:] == (1, 2)


@pytest.mark.parametrize(
    "model,preset",
    [
        ("kimi/kimi-k2.5", "off"),
        ("kimi/kimi-k3", "low"),
        ("deepseek/deepseek-flash", "off"),
    ],
)
def test_catalog_minimum(model: str, preset: str) -> None:
    assert minimum_model_runtime_config(model).reasoning_preset == preset


def test_failed_generation_uses_fallback_once_and_expiration_prevents_presentation(
    tmp_path: Path,
) -> None:
    svc, _generator = service(tmp_path)
    calls: list[int] = []

    def fail(*args: Any, **kwargs: Any) -> str:
        calls.append(1)
        raise TimeoutError("deadline")

    svc.generator = cast(NotificationGenerator, SimpleNamespace(generate=fail))
    item = svc.accept(fact())
    assert svc.run_once()
    ready = svc.store.get(item.notification_id)
    assert ready.text_source == "fallback"
    assert not svc.run_once()
    assert calls == [1]
    svc.clock = lambda: NOW + timedelta(days=2)
    with pytest.raises(NotificationConflict):
        svc.prepare(item.notification_id, version=ready.version, window_id="bubble")
    assert svc.store.get(item.notification_id).generation_status == "expired"


def test_ambient_is_provided_and_does_not_call_notification_model(tmp_path: Path) -> None:
    svc, generator = service(tmp_path)
    svc.sessions.append_event(
        "s",
        Event(
            type="assistant_message",
            uuid="a",
            ts=NOW,
            payload={"content": "该休息一下啦。"},
            meta={"run_id": "ambient-run"},
        ),
    )
    snapshot = RunSnapshot(
        run_id="ambient-run",
        session_id="s",
        status=RunStatus.TERMINAL,
        outcome=RunOutcome.COMPLETED,
        reason_code="end_turn",
        channel="text",
        origin="system_trigger",
        control_scope=None,
        created_at=NOW,
        started_at=NOW,
        finished_at=NOW,
        version=3,
        source_kind="cron:bedtime",
        presentation="ambient",
        output_visibility="user",
    )
    svc.on_terminal(snapshot)
    svc.run_once()
    item = svc.store.list()[0]
    assert item.fact.kind == "ambient"
    assert item.text_source == "provided"
    assert item.text == "该休息一下啦。"
    assert generator.calls == 0


def test_approval_resolution_invalidates_prepared_notification(tmp_path: Path) -> None:
    svc, generator = service(tmp_path)
    pending = [SimpleNamespace(request_id="approval-1")]
    svc.approvals = cast(Any, SimpleNamespace(pending=lambda: tuple(pending)))
    payload: dict[str, object] = {
        "request_id": "approval-1",
        "session_id": "s",
        "run_id": "r",
        "status": "pending",
    }
    svc.on_approval(payload)
    svc.run_once()
    item = svc.store.list()[0]
    prepared = svc.prepare(item.notification_id, version=item.version, window_id="bubble")
    pending.clear()
    svc.on_approval({**payload, "status": "approved"})
    with pytest.raises(NotificationConflict):
        svc.presented(
            item.notification_id,
            attempt=prepared.delivery["attempt"],
            text=item.text,
            truncated=False,
        )
    assert svc.store.get(item.notification_id).delivery["status"] == "invalidated"
    assert generator.calls == 1
