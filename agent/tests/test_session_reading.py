from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.sessions.reading import EventFilter, session_reader

from agent import Event, JsonlSessionStore, Session


def history(root: Path, count: int = 150) -> JsonlSessionStore:
    store = JsonlSessionStore(root)
    store.create(Session.new("Read", "default", "test/model", session_id="one"))
    store.append_events(
        "one",
        [
            Event("user_message", f"u{i}", datetime.now(UTC), {"content": "x" * 1000})
            for i in range(count)
        ],
    )
    store.discard_business_index("one")
    return store


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("ordinary reader hydrated complete history")


def test_cold_bootstrap_streams_once_and_warm_points_deserialize_only_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path)
    for name in ("load", "load_business", "query", "_read_records"):
        monkeypatch.setattr(store, name, forbidden)
    with session_reader(store).snapshot("one") as read:
        assert read.event("u140").uuid == "u140"  # type: ignore[union-attr]
        assert read.event_count == 151
    fresh = JsonlSessionStore(tmp_path)
    monkeypatch.setattr(fresh, "iter_events", forbidden)
    original = Event.from_jsonl
    materialized = []

    def counted(line: str) -> Event:
        result = original(line)
        materialized.append(result.uuid)
        return result

    monkeypatch.setattr(Event, "from_jsonl", counted)
    with fresh.reader.snapshot("one") as read:
        assert read.contains(EventFilter(ids=("u140",)))
        assert read.positions(EventFilter(ids=("u140", "u141"))) == {"u140": 141, "u141": 142}
        assert read.event("u141").uuid == "u141"  # type: ignore[union-attr]
    assert materialized == ["u141"]


def test_rewrite_updates_selected_ids_without_hydrating_old_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path)
    assert store.reader.event("one", "u10") is not None
    monkeypatch.setattr(store._business_index, "_snapshot", forbidden)
    monkeypatch.setattr(store, "iter_events", forbidden)
    store.append_event(
        "one",
        Event("turn_rewrite", "rewrite", datetime.now(UTC), {"inactive_event_uuids": ["u10"]}),
    )
    with store.reader.snapshot("one") as read:
        assert read.event("u10") is None
        assert read.event("u10", active=False) is not None
        assert read.history_epoch.endswith(":rewrite")
        assert read.event("u11") is not None


def test_cancelled_bootstrap_is_not_published(tmp_path: Path) -> None:
    store = history(tmp_path)
    checks = 0

    def cancel() -> None:
        nonlocal checks
        checks += 1
        if checks > 30:
            raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError), store.reader.snapshot("one", cancel_check=cancel):
        pass
    with store.reader.snapshot("one") as read:
        assert read.event_count == 151


def test_snapshot_rejects_concurrent_append(tmp_path: Path) -> None:
    store = history(tmp_path, 2)
    with pytest.raises(ValueError, match="history_changed"), store.reader.snapshot("one") as read:
        store.append_event(
            "one", Event("user_message", "new", datetime.now(UTC), {"content": "new"})
        )
        assert read.event("new") is None


def test_state_does_not_bootstrap_business_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path)
    monkeypatch.setattr(store, "_history_version", forbidden)
    monkeypatch.setattr(store, "iter_events", forbidden)
    assert store.reader.exists("one")
    assert not store.reader.exists("missing")
    assert store.reader.state("one").current_model == "test/model"
    assert store.reader.identity("one").initial_title == "Read"


def test_batch_identity_read_keeps_all_chunks_and_omits_missing_ids(tmp_path: Path) -> None:
    store = history(tmp_path, 1001)
    identities = tuple(f"u{i}" for i in range(1001))
    selected = store.reader.events_by_id("one", (*identities, "u0", "missing"))
    assert set(selected) == set(identities)
    assert store.reader.events_by_id("missing-session", ()) == {}


def test_point_and_latest_sql_cost_does_not_grow_with_unrelated_bodies(tmp_path: Path) -> None:
    store = history(tmp_path, 2)

    def measure() -> int:
        with store.reader.snapshot("one") as read:
            steps = 0

            def progress() -> int:
                nonlocal steps
                steps += 1
                return 0

            read._db.set_progress_handler(progress, 1)
            assert read.event("u0") is not None
            assert read.latest("user_message").uuid == "u1"  # type: ignore[union-attr]
            return steps

    before = measure()
    store.append_events(
        "one",
        [
            Event("tool_call_result", f"t{i}", datetime.now(UTC), {"content": "unrelated" * 1000})
            for i in range(1500)
        ],
    )
    after = measure()
    assert after <= before + 30, (before, after)


def test_preview_does_not_run_total_counts(tmp_path: Path) -> None:
    store = history(tmp_path)
    from agent.sessions.message_access import read_message_page

    with store.reader.snapshot("one") as read:
        statements: list[str] = []
        read._db.set_trace_callback(statements.append)
        page = read_message_page(read._db, read.source_version, limit=1, include_counts=False)
        assert len(page.messages) == 1
        assert not any("COUNT(" in statement for statement in statements)


def test_rollout_reference_ranges_keep_rewrite_evidence(tmp_path: Path) -> None:
    store = history(tmp_path, 1)
    store.append_event("one", Event("model_context", "internal", datetime.now(UTC), {}))
    store.append_event(
        "one", Event("assistant_message", "end", datetime.now(UTC), {"content": "reply"})
    )
    store.append_event(
        "one",
        Event("turn_rewrite", "rewrite", datetime.now(UTC), {"inactive_event_uuids": ["internal"]}),
    )
    with store.reader.snapshot("one") as read:
        start, end = read.source_range("u0", "end")  # type: ignore[misc]
        assert read.inactive_in_range(start, end)


def test_rewritten_configuration_invalidates_summary_without_rewriting_activity(
    tmp_path: Path,
) -> None:
    store = history(tmp_path, 1)
    original = store.list()[0]
    store.append_events(
        "one",
        [
            Event("model_change", "model", datetime.now(UTC), {"to": "deepseek/deepseek-chat"}),
            Event("turn_rewrite", "undo", datetime.now(UTC), {"inactive_event_uuids": ["model"]}),
        ],
    )
    assert store.list()[0].model == original.model
    assert store.reader.state("one").current_model == original.model


def test_warm_point_read_does_not_wait_for_full_index_owner(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = history(tmp_path, 1)
    assert store.reader.event("one", "u0") is not None
    with ThreadPoolExecutor() as pool, store._business_index.database("one"):
        future = pool.submit(store.reader.event, "one", "u0")
        assert future.result(timeout=3) is not None


def test_recall_text_only_reads_relevant_positions(tmp_path: Path) -> None:
    store = history(tmp_path, 2)
    store.append_event(
        "one", Event("tool_call_result", "tool", datetime.now(UTC), {"content": "not recall text"})
    )
    assert set(store.reader.recall_text("one").raw_event_indices) == {"u0", "u1"}
    fresh = JsonlSessionStore(tmp_path)
    assert set(fresh.reader.recall_text("one", boundary_id="tool").raw_event_indices) == {
        "u0",
        "u1",
        "tool",
    }


def test_read_cost_logs_do_not_expose_content_or_change_results(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.sessions.reading import observation

    store = history(tmp_path, 1)
    caplog.set_level("DEBUG", logger=observation.__name__)
    store.reader.event("one", "u0")
    messages = [r.message for r in caplog.records if r.name == observation.__name__]
    assert len(messages) == 1 and "mode=point" in messages[0] and "rows=1" in messages[0]
    assert "x" * 100 not in messages[0] and str(tmp_path) not in messages[0]
    monkeypatch.setattr(observation.logger, "debug", forbidden)
    assert store.reader.event("one", "u0") is not None
