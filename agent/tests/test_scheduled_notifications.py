from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agent.scheduled_tasks import (
    ScheduledOccurrenceOutcome,
    ScheduledTaskError,
    ScheduledTaskService,
    ScheduledTaskStore,
)
from agent.scheduling import FixedIntervalSchedule
from agent.tools.builtin.scheduled_task_manage import ScheduledTaskManageTool

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def test_flag_updates_snapshots_and_legacy_migration(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    store = ScheduledTaskStore(path)
    service = ScheduledTaskService(store, clock=lambda: NOW)
    task = service.create(
        name="test",
        instruction="work",
        persona_name="default",
        model_id="fake",
        target_session_id="s",
        schedule=FixedIntervalSchedule(NOW + timedelta(hours=1), 3600),
    )
    assert not task.notify_bound_im
    old = service.run_now(task.id)
    task = service.update(task.id, revision=task.revision, notify_bound_im=True)
    assert not store.get_occurrence(old.id).task_snapshot.notify_bound_im
    task = service.update(task.id, revision=task.revision, name="renamed")
    assert task.notify_bound_im
    # Second task proves the flag is independent within the same session.
    other = service.create(
        name="other",
        instruction="work",
        persona_name="default",
        model_id="fake",
        target_session_id="s",
        schedule=task.schedule,
    )
    assert not other.notify_bound_im
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE scheduled_occurrences SET state='terminal',outcome='completed'")
    run = service.run_now(task.id)
    assert run.task_snapshot.notify_bound_im
    assert store.notifications.summary(run.id)["state"] == "pending"
    task = service.update(task.id, revision=task.revision, notify_bound_im=False)
    assert not task.notify_bound_im
    assert store.get_occurrence(run.id).task_snapshot.notify_bound_im
    # Reproduce pre-upgrade schema and pre-upgrade snapshots, not just default constructors.
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE scheduled_notification_deliveries")
        conn.execute("DROP TABLE scheduled_notifications")
        conn.execute("ALTER TABLE scheduled_tasks DROP COLUMN notify_bound_im")
        for key, raw in conn.execute(
            "SELECT id,task_snapshot_json FROM scheduled_occurrences"
        ).fetchall():
            snapshot = json.loads(raw)
            snapshot.pop("notify_bound_im")
            conn.execute(
                "UPDATE scheduled_occurrences SET task_snapshot_json=? WHERE id=?",
                (json.dumps(snapshot), key),
            )
    migrated = ScheduledTaskStore(path)
    assert not migrated.get_task(task.id).notify_bound_im
    assert not migrated.get_occurrence(run.id).task_snapshot.notify_bound_im
    assert migrated.notifications.pending_ids() == []
    assert migrated.notifications.summary(old.id)["state"] == "disabled"


def test_all_trigger_kinds_register_notifications_and_recover_interruptions(tmp_path: Path) -> None:
    for hours, expected in [(1, "scheduled"), (3, "catch_up")]:
        store = ScheduledTaskStore(tmp_path / f"tasks-{hours}.db")
        service = ScheduledTaskService(store, clock=lambda: NOW)
        task = service.create(
            name="test",
            instruction="work",
            persona_name="default",
            model_id="fake",
            target_session_id="s",
            schedule=FixedIntervalSchedule(NOW + timedelta(hours=1), 3600),
            notify_bound_im=True,
        )
        due = NOW + timedelta(hours=hours)
        key = store.create_due_occurrences(due)[0]
        claim = store.claim_pending(due)[0]
        assert claim.occurrence.trigger_kind.value == expected
        store.mark_submitted(key, now=due, skill_source=None, skill_version=None)
        assert store.notifications.pending_ids() == []
        store.recover_startup(due)
        assert store.notifications.pending_ids() == [key]
        assert store.get_occurrence(key).outcome is ScheduledOccurrenceOutcome.INTERRUPTED
        manual = service.run_now(task.id)
        assert manual.trigger_kind.value == "manual"
        assert store.notifications.summary(manual.id)["state"] == "pending"


def test_tool_create_update_query_and_strict_boolean_share_state(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "tasks.db")
    service = ScheduledTaskService(store, clock=lambda: NOW)
    tool = ScheduledTaskManageTool(
        service, default_persona_id=None, default_persona_name="default", default_model_id="fake"
    )
    created = tool.invoke(
        {
            "operation": "create",
            "name": "test",
            "instruction": "work",
            "target_session_id": "s",
            "schedule": {"kind": "fixed_interval", "interval": 1, "unit": "hour"},
            "notify_bound_im": True,
        }
    )
    assert not created.is_error
    task = json.loads(created.text)["task"]
    assert task["notify_bound_im"] is True
    updated = tool.invoke(
        {
            "operation": "update",
            "task_id": task["id"],
            "revision": task["revision"],
            "notify_bound_im": False,
        }
    )
    assert not updated.is_error
    assert json.loads(updated.text)["task"]["notify_bound_im"] is False
    listed = json.loads(tool.invoke({"operation": "list"}).text)
    assert listed["tasks"][0]["notify_bound_im"] is False
    with pytest.raises(ScheduledTaskError, match="notify_bound_im"):
        service.update(task["id"], revision=task["revision"] + 1, notify_bound_im="false")  # type: ignore[arg-type]
