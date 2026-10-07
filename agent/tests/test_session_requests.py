import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.runtime.inbox import UserEvent
from agent.runtime.models import RunHandle, RunRequest
from agent.scheduled_tasks import ScheduledTaskService, ScheduledTaskStore
from agent.scheduling import OnceSchedule
from agent.session_requests import SessionRequestStore


def test_receipt_is_idempotent_and_rejects_changed_input(tmp_path: Path) -> None:
    store = SessionRequestStore(tmp_path / "requests.db")
    payload = {"text": "hello", "options": {"attachments": [{"asset_id": "asset"}]}}
    assert store.admit("r", "s", "u", payload)
    assert not store.admit("r", "s", "u", payload)
    with pytest.raises(ValueError, match="request_id_conflict"):
        store.admit("r", "other", "u", payload)
    assert store.referenced_assets() == {"asset"}
    store.settle("r", "cancelled")
    assert not store.referenced_assets()


def test_receipt_recovery_does_not_replay_or_overwrite_completed(tmp_path: Path) -> None:
    path = tmp_path / "requests.db"
    store = SessionRequestStore(path)
    for run_id in ("queued", "persisted", "completed"):
        store.admit(run_id, "s", run_id, {"text": run_id})
    store.settle("completed", "completed", "end_turn")
    restored = SessionRequestStore(path)
    restored.recover(
        lambda _session, run: ("completed", "end_turn") if run == "persisted" else None
    )
    for run_id, outcome in (
        ("queued", "interrupted"),
        ("persisted", "completed"),
        ("completed", "completed"),
    ):
        receipt = restored.get(run_id)
        assert receipt is not None and receipt["outcome"] == outcome
    handle = RunHandle(
        RunRequest(event=UserEvent(session_id="s", user_input="queued"), run_id="queued")
    )
    handle.publish_initial()
    restored.observe(handle.snapshot())
    receipt = restored.get("queued")
    assert receipt is not None and receipt["status"] == "terminal"


def test_schedule_recovery_preserves_durable_request_outcome(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = ScheduledTaskStore(tmp_path / "tasks.db")
    service = ScheduledTaskService(store, clock=lambda: now)
    task = service.create(
        name="test",
        instruction="hello",
        target_session_id="s",
        schedule=OnceSchedule(datetime(2027, 1, 1), "UTC"),
        persona_name="default",
        model_id="fake",
    )
    occurrence = service.run_now(task.id)
    claimed = store.claim_pending(now)[0].occurrence
    store.mark_submitted(claimed.id, now=now, skill_source=None, skill_version=None)
    store.recover_startup(
        now, lambda run: ("completed", "end_turn") if run == claimed.run_id else None
    )
    assert store.get_occurrence(occurrence.id).outcome == "completed"


def test_legacy_tasks_are_paused_without_guessing_target(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = ScheduledTaskStore(path)
    service = ScheduledTaskService(store, clock=lambda: now)
    task = service.create(
        name="legacy",
        instruction="hello",
        target_session_id="old-target",
        origin_session_id="creation-source",
        schedule=OnceSchedule(datetime(2027, 1, 1), "UTC"),
        persona_name="default",
        model_id="custom/legacy-model",
    )
    occurrence = service.run_now(task.id)
    with sqlite3.connect(path) as conn:
        snapshot = json.loads(
            conn.execute("SELECT task_snapshot_json FROM scheduled_occurrences").fetchone()[0]
        )
        snapshot.pop("target_session_id")
        snapshot.pop("request_options")
        conn.execute(
            "UPDATE scheduled_occurrences SET task_snapshot_json=?", (json.dumps(snapshot),)
        )
        for column in ("target_session_id", "request_options_json", "pause_reason"):
            conn.execute(f"ALTER TABLE scheduled_tasks DROP COLUMN {column}")
    migrated = ScheduledTaskStore(path)
    restored = migrated.get_task(task.id)
    assert restored.status == "paused" and restored.target_session_id is None
    assert restored.origin_session_id == "creation-source"
    assert restored.request_options.model is not None
    assert restored.request_options.model.model_id == "custom/legacy-model"
    assert migrated.claim_pending(now) == []
    assert migrated.get_occurrence(occurrence.id).reason_code == "target_confirmation_required"
    assert ScheduledTaskStore(path).get_task(task.id) == restored
