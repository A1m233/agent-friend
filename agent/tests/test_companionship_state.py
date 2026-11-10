from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from agent.companionship.config import CompanionshipConfig
from agent.companionship.snapshot import capture_changes, render_overview
from agent.companionship.store import CompanionshipStore
from agent.sessions.access.service import ConversationAccessError, ConversationAccessService

from agent import Event, JsonlSessionStore, Session

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def test_random_delay_samples_configured_range_each_time(monkeypatch: pytest.MonkeyPatch) -> None:
    delays = iter([1200, 1777, 2400])

    def draw(low: int, high: int) -> int:
        assert (low, high) == (1200, 2400)
        return next(delays)

    monkeypatch.setattr("agent.companionship.config.random.randint", draw)
    config = CompanionshipConfig.from_settings(
        {"companionshipLimits": {"minimum_interval_minutes": 20, "interval_minutes": 40}}
    )
    assert [config.random_delay_seconds() for _ in range(3)] == [1200, 1777, 2400]


@pytest.mark.parametrize(("requested", "actual"), [(1, 600), (1777, 1777), (86400, 3600)])
def test_explicit_wake_respects_both_bounds_and_survives_finish(
    tmp_path: Path, requested: int, actual: int
) -> None:
    store = CompanionshipStore(tmp_path / "state.db")
    assert store.begin("r1", {}, now=NOW)
    store.schedule(
        "r1",
        now=NOW,
        wake_at=NOW + timedelta(seconds=requested),
        minimum_seconds=600,
        maximum_seconds=3600,
    )
    store.finish("r1", outcome="completed", now=NOW, fallback_seconds=1999)
    assert (
        CompanionshipStore(store.path).read()["next_wake_at"]
        == (NOW + timedelta(seconds=actual)).isoformat()
    )


def test_wake_state_persists_pause_usage_and_no_double_run(tmp_path: Path) -> None:
    store = CompanionshipStore(tmp_path / "state.db")
    store.control("enable", now=NOW)
    store.ensure_inner(lambda: "inner")
    assert store.ensure_inner(lambda: "another") == "inner"
    snapshot: dict[str, Any] = {"baselines": {}, "sessions": {}, "entries": []}
    assert store.begin("r1", snapshot, now=NOW)
    assert not store.begin("r2", snapshot, now=NOW)
    for _ in range(101):
        store.record_call("r1", now=NOW + timedelta(hours=2))
    assert store.daily_calls("2026-11-03") == 101
    store.control("pause_contact", now=NOW, until=(NOW + timedelta(hours=1)).isoformat())
    restarted = CompanionshipStore(store.path)
    assert restarted.paused(restarted.read(), "contact", NOW)
    assert not restarted.paused(restarted.read(), "activity", NOW)
    restarted.finish("r1", outcome="interrupted", now=NOW, fallback_seconds=600)
    assert restarted.read()["next_wake_at"] == (NOW + timedelta(minutes=10)).isoformat()
    assert not restarted.begin("r2", snapshot, now=NOW)
    restarted.control("pause_activity", now=NOW)
    assert not restarted.begin("r2", snapshot, now=NOW + timedelta(hours=1))


def test_snapshot_acknowledgement_and_exact_increment(tmp_path: Path) -> None:
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.create(Session.new("Daily", "default", "test/model", session_id="daily"))
    sessions.append_event(
        "daily", Event(type="user_message", uuid="old", ts=NOW, payload={"content": "old body"})
    )
    access = ConversationAccessService(sessions)
    state = CompanionshipStore(tmp_path / "state.db")
    state.control("enable", now=NOW)
    first = capture_changes(access, state.read(), "r1")
    assert first["entries"][0]["counts"]["user"] == 0
    assert "old body" not in str(first)
    assert render_overview(access, first)["conversations"][0]["preview"] == "old body"
    state.begin("r1", first, now=NOW)
    assert state.read()["baselines"] == {}
    state.acknowledge("r1", now=NOW)
    state.finish("r1", outcome="completed", now=NOW, fallback_seconds=600)
    sessions.append_event(
        "daily",
        Event(
            type="user_message",
            uuid="new",
            ts=NOW + timedelta(seconds=1),
            payload={"content": "new body"},
        ),
    )
    second = capture_changes(access, state.read(), "r2")
    assert second["entries"][0]["counts"]["user"] == 1
    sessions.append_event(
        "daily",
        Event(
            type="user_message",
            uuid="later",
            ts=NOW + timedelta(seconds=2),
            payload={"content": "later body"},
        ),
    )
    access.wake_snapshot = lambda _sid: second
    assert [
        message["message_id"]
        for message in access.read_conversation(session_id="daily", mode="since_wake")["messages"]
    ] == ["new"]


def test_controls_validate_time_and_deletion_does_not_reenable(tmp_path: Path) -> None:
    state = CompanionshipStore(tmp_path / "state.db")
    with pytest.raises(ValueError):
        state.control("pause_activity", now=NOW, until="2026-11-04T00:00:00")
    state.control("enable", now=NOW)
    state.ensure_inner(lambda: "inner")
    state.forget_session("inner")
    assert not state.read()["enabled"]
    assert state.read()["inner_session_id"] is None


@pytest.mark.parametrize(("rebuild", "legacy"), [(False, False), (True, False), (True, True)])
def test_index_rebuild_preserves_wake_counts_and_read_range(
    tmp_path: Path, rebuild: bool, legacy: bool
) -> None:
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.create(Session.new("长会话", "default", "test/model", session_id="daily"))
    sessions.append_event(
        "daily", Event(type="assistant_message", uuid="old", ts=NOW, payload={"content": "old"})
    )
    access = ConversationAccessService(sessions)
    store = CompanionshipStore(tmp_path / "state.db")
    first = capture_changes(access, store.read(), "r1")
    if legacy:
        del first["baselines"]["daily"]["last_message_id"]
    assert store.begin("r1", first, now=NOW)
    store.acknowledge("r1", now=NOW)
    store.finish("r1", outcome="completed", now=NOW, fallback_seconds=0)
    messages = []
    for i in range(4):
        ts = NOW + timedelta(minutes=i + 1)
        messages.extend(
            [
                Event(type="user_message", uuid=f"u{i}", ts=ts, payload={"content": "more"}),
                Event(type="assistant_message", uuid=f"a{i}", ts=ts, payload={"content": "reply"}),
            ]
        )
    sessions.append_events("daily", messages)
    if rebuild:
        sessions.discard_business_index("daily")
    state = CompanionshipStore(store.path).read()
    second = capture_changes(access, state, "r2")
    assert (second["sessions"]["daily"]["epoch"] != first["sessions"]["daily"]["epoch"]) == rebuild
    overview = render_overview(access, second)["conversations"][0]
    assert overview["change"] == "updated"
    assert overview["counts"] == {"user": 4, "assistant": 4, "proactive": 0, "assistant_replies": 4}
    access.wake_snapshot = lambda _: second
    assert [
        item["message_id"]
        for item in access.read_conversation(session_id="daily", mode="since_wake")["messages"]
    ] == [m.uuid for m in messages]


@pytest.mark.parametrize("cause", ["rewrite", "missing_anchor", "unverifiable_legacy"])
def test_unverifiable_boundary_reports_unknown_then_resumes(tmp_path: Path, cause: str) -> None:
    sessions = JsonlSessionStore(tmp_path / "sessions")
    sessions.create(Session.new("Daily", "default", "test/model", session_id="daily"))
    sessions.append_event(
        "daily", Event(type="user_message", uuid="old", ts=NOW, payload={"content": "old"})
    )
    access = ConversationAccessService(sessions)
    state = CompanionshipStore(tmp_path / "state.db").read()
    first = capture_changes(access, state, "r1")
    state.update(baselines=first["baselines"], last_overview_at=NOW.isoformat())
    if cause == "rewrite":
        sessions.append_event(
            "daily",
            Event(
                type="turn_rewrite", uuid="edit", ts=NOW, payload={"inactive_event_uuids": ["old"]}
            ),
        )
    elif cause == "missing_anchor":
        state["baselines"]["daily"]["last_message_id"] = "no-longer-present"
    else:
        del state["baselines"]["daily"]["last_message_id"]
    sessions.append_event(
        "daily",
        Event(
            type="user_message",
            uuid="new",
            ts=NOW + timedelta(minutes=1),
            payload={"content": "new"},
        ),
    )
    sessions.discard_business_index("daily")
    second = capture_changes(access, state, "r2")
    entry = render_overview(access, second)["conversations"][0]
    assert entry["change"] == "history_changed"
    assert entry["counts"] is None
    assert entry["preview"] == "new"
    access.wake_snapshot = lambda _: second
    with pytest.raises(ConversationAccessError, match="latest"):
        access.read_conversation(session_id="daily", mode="since_wake")
    assert (
        access.read_conversation(session_id="daily", limit=1)["messages"][0]["message_id"] == "new"
    )
    state["baselines"] = second["baselines"]
    sessions.append_event(
        "daily",
        Event(
            type="user_message",
            uuid="later",
            ts=NOW + timedelta(minutes=2),
            payload={"content": "later"},
        ),
    )
    assert capture_changes(access, state, "r3")["entries"][0]["counts"]["user"] == 1


@pytest.mark.parametrize("explicit_disable", [True, False])
def test_preview_migration_only_enables_untouched_defaults(
    tmp_path: Path, explicit_disable: bool
) -> None:
    import json
    import sqlite3

    from agent.companionship.store import initial_state

    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE state(id INTEGER PRIMARY KEY,body TEXT NOT NULL);
            CREATE TABLE wakes(run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, deadline TEXT NOT NULL,
                status TEXT NOT NULL, snapshot TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
                model_calls INTEGER NOT NULL DEFAULT 0, usage TEXT NOT NULL DEFAULT '{}');
        """)
        before = initial_state() | {"enabled": False, "version": int(explicit_disable)}
        db.execute("INSERT INTO state VALUES(1,?)", (json.dumps(before),))
    assert CompanionshipStore(path).read()["enabled"] is not explicit_disable
    assert CompanionshipStore(path).read()["enabled"] is not explicit_disable
