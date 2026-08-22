from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from agent.runtime import (
    RunHandle,
    RunOutcome,
    RunPriority,
    RunRequest,
    TurnStatus,
    UserEvent,
)
from agent.scheduled_tasks import (
    ScheduledOccurrenceOutcome,
    ScheduledOccurrenceState,
    ScheduledTaskService,
    ScheduledTaskStore,
)
from agent.scheduling import OnceSchedule
from agent.skills import SkillCatalogService
from agent_bridge.scheduled_tasks import ScheduledRunTracker, ScheduledTaskRunOrchestrator

from agent import JsonlSessionStore, SessionCatalog, SessionManager

NOW = datetime(2026, 8, 23, 8, tzinfo=UTC)


class _Runtime:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, event, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        request = RunRequest(
            event=event,
            run_id=kwargs["run_id"],
            priority=kwargs["priority"],
            metadata=kwargs["metadata"],
            conversation_runner=kwargs["conversation_runner"],
            consumer_abandon_policy=kwargs["consumer_abandon_policy"],
            control_scope=kwargs["control_scope"],
        )
        handle = RunHandle(request, primary_consumer=False)
        handle.publish_initial()
        handle.mark_running()
        handle.finish_once(
            RunOutcome.COMPLETED,
            reason_code="end_turn",
            turn_status=TurnStatus.COMPLETED,
        )
        return handle


def _wait_terminal(store: ScheduledTaskStore, occurrence_id: str) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if store.get_occurrence(occurrence_id).state is ScheduledOccurrenceState.TERMINAL:
            return
        time.sleep(0.01)
    raise AssertionError("occurrence did not become terminal")


def test_runner_creates_hidden_origin_session_and_persists_terminal(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    service = ScheduledTaskService(store, clock=lambda: NOW)
    task = service.create(
        name="日报",
        instruction="总结今天",
        schedule=OnceSchedule(datetime(2026, 8, 24, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )
    occurrence = service.run_now(task.id)
    session_store = JsonlSessionStore(tmp_path / "sessions")
    sessions = SessionManager(session_store)
    runtime = _Runtime()
    tracker = ScheduledRunTracker(store, clock=lambda: NOW, poll_seconds=0.01)
    tracker.start()
    claim = store.claim_pending(NOW)[0]
    runner = ScheduledTaskRunOrchestrator(
        store=store,
        sessions=sessions,
        agent_runtime=runtime,  # type: ignore[arg-type]
        tracker=tracker,
        skill_service=None,
        clock=lambda: NOW,
    )

    runner.dispatch_batch((claim,))
    _wait_terminal(store, occurrence.id)
    tracker.stop()

    saved = store.get_occurrence(occurrence.id)
    assert saved.outcome is ScheduledOccurrenceOutcome.COMPLETED
    assert saved.session_id and saved.run_id
    session = sessions.open(saved.session_id)
    assert session.origin_kind == "scheduled_task"
    assert session.origin_id == task.id
    assert sessions.list() == []
    assert sessions.latest() is None
    assert SessionCatalog(session_store).list_all() == ()
    assert sessions.list(include_scheduled=True)[0].session_id == saved.session_id


def test_bound_skill_without_service_fails_before_runtime_submit(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    service = ScheduledTaskService(store, clock=lambda: NOW)
    task = service.create(
        name="日报",
        instruction="总结今天",
        schedule=OnceSchedule(datetime(2026, 8, 24, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
        skill_id="builtin/example",
    )
    occurrence = service.run_now(task.id)
    runtime = _Runtime()
    tracker = ScheduledRunTracker(store, clock=lambda: NOW)
    tracker.start()
    claim = store.claim_pending(NOW)[0]
    runner = ScheduledTaskRunOrchestrator(
        store=store,
        sessions=SessionManager(JsonlSessionStore(tmp_path / "sessions")),
        agent_runtime=runtime,  # type: ignore[arg-type]
        tracker=tracker,
        skill_service=None,
        clock=lambda: NOW,
    )

    runner.dispatch_batch((claim,))
    tracker.stop()
    saved = store.get_occurrence(occurrence.id)
    assert runtime.calls == 0
    assert saved.outcome is ScheduledOccurrenceOutcome.FAILED
    assert saved.reason_code == "skill_service_unavailable"


def test_bound_skill_records_frozen_source_and_version(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills" / "scheduled-test"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: scheduled-test\ndescription: Scheduled test workflow\n---\n\nDo the test.\n",
        encoding="utf-8",
    )
    skill_service = SkillCatalogService(
        personal_root=tmp_path / "skills",
        shared_root=tmp_path / "shared-skills",
        watcher_enabled=False,
    )
    catalog = skill_service.initialize()
    skill = next(item for item in catalog.entries if item.name == "scheduled-test")
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    service = ScheduledTaskService(store, clock=lambda: NOW)
    task = service.create(
        name="Skill 日报",
        instruction="执行 Skill",
        schedule=OnceSchedule(datetime(2026, 8, 24, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
        skill_id=skill.id,
    )
    occurrence = service.run_now(task.id)
    tracker = ScheduledRunTracker(store, clock=lambda: NOW, poll_seconds=0.01)
    tracker.start()
    runner = ScheduledTaskRunOrchestrator(
        store=store,
        sessions=SessionManager(JsonlSessionStore(tmp_path / "sessions")),
        agent_runtime=_Runtime(),  # type: ignore[arg-type]
        tracker=tracker,
        skill_service=skill_service,
        clock=lambda: NOW,
    )

    runner.dispatch_batch((store.claim_pending(NOW)[0],))
    _wait_terminal(store, occurrence.id)
    tracker.stop()
    saved = store.get_occurrence(occurrence.id)
    assert saved.skill_source == "personal"
    assert saved.skill_version == skill.version


def test_tracker_stop_persists_final_terminal_snapshot(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    tracker = ScheduledRunTracker(store, clock=lambda: NOW, poll_seconds=60)
    tracker.start()
    service = ScheduledTaskService(store, clock=lambda: NOW)
    task = service.create(
        name="立即完成",
        instruction="执行",
        schedule=OnceSchedule(datetime(2026, 8, 24, 9), "UTC"),
        persona_name="default",
        model_id="fake/model",
    )
    occurrence = service.run_now(task.id)
    claimed = store.claim_pending(NOW)[0].occurrence
    store.mark_submitted(
        claimed.id,
        now=NOW,
        skill_source=None,
        skill_version=None,
    )
    request = RunRequest(
        event=UserEvent(session_id=claimed.session_id or "session", user_input="执行"),
        run_id=claimed.run_id or "run",
        priority=RunPriority.PROACTIVE,
    )
    handle = RunHandle(request, primary_consumer=False)
    handle.publish_initial()
    handle.mark_running()
    handle.finish_once(
        RunOutcome.COMPLETED,
        reason_code="end_turn",
        turn_status=TurnStatus.COMPLETED,
    )
    tracker.track(occurrence.id, handle)

    assert tracker.stop()
    assert store.get_occurrence(occurrence.id).outcome is ScheduledOccurrenceOutcome.COMPLETED
