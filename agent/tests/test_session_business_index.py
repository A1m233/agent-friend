from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import Event as Signal
from types import SimpleNamespace

import pytest
from agent.sessions import rollout_encoding
from agent.sessions.events import EventType

from agent import Event, JsonlSessionStore, Session, SessionCorruptError


def event(event_type: EventType, uuid: str, **payload: object) -> Event:
    return Event(
        type=event_type, uuid=uuid, ts=datetime.now(UTC), payload=payload, meta={"run_id": "run"}
    )


def make_store(root: Path) -> JsonlSessionStore:
    store = JsonlSessionStore(root)
    store.create(Session.new("History", "default", "test/model", session_id="one"))
    return store


def forbid(*args: object, **kwargs: object) -> None:
    raise AssertionError("existing history must not be scanned")


def test_persistent_projection_survives_new_store_and_memory_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.append_events(
        "one",
        [
            event("user_message", "user", content="hello"),
            event("assistant_message", "answer", content="world"),
        ],
    )
    original = store.query("one")
    fresh = JsonlSessionStore(tmp_path, query_cache_entries=0, query_cache_bytes=0)
    monkeypatch.setattr(fresh, "_read_records", forbid)
    assert fresh.query("one").active_events == original.active_events
    assert fresh.load_business("one").active_events == list(original.active_events)
    # Page reads do not hydrate the full business projection either.
    monkeypatch.setattr(fresh, "query", forbid)
    assert fresh.query_page("one", turn_limit=30).active_events[-1].uuid == "answer"


def test_damaged_database_rebuilds_and_source_corruption_remains_an_error(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_event("one", event("user_message", "u", content="ok"))
    expected = store.query_page("one", turn_limit=30).active_events
    store._business_index.path("one").write_bytes(b"not sqlite")
    fresh = JsonlSessionStore(tmp_path)
    assert fresh.query_page("one", turn_limit=30).active_events == expected
    path = tmp_path / "one.jsonl"
    path.write_bytes(path.read_bytes() + b"not JSON\n")
    with pytest.raises(SessionCorruptError):
        JsonlSessionStore(tmp_path).query_page("one", turn_limit=30)


def test_business_decode_validates_references_without_copying_model_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    encoder = rollout_encoding.RolloutEncoder()
    encoded = encoder.encode(
        event(
            "rollout_request_prepared",
            "prepared",
            model_context={"large": "x" * 100000},
            epoch_manifest={"envelope": {"a": "b"}},
        )
    )
    store.append_events("one", encoded)
    monkeypatch.setattr(rollout_encoding, "copy", SimpleNamespace(deepcopy=forbid))
    assert store.query_page("one", turn_limit=30).active_events == (
        store.query("one").active_events[0],
    )
    store.discard_business_index("one")
    # The same optimized reader still checks the internal value's digest.
    store.append_event(
        "one",
        event(
            "rollout_value",
            "bad",
            encoding_version=1,
            kind="model_context",
            sha256="wrong",
            value={},
        ),
    )
    with pytest.raises(SessionCorruptError, match="digest"):
        store.query_page("one", turn_limit=30)


def test_append_during_initial_build_publishes_caught_up_index_without_rescan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    entered, release = Signal(), Signal()
    original = store._read_records
    calls = 0

    def scan(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        calls += 1
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(store, "_read_records", scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(store.query_page, "one", turn_limit=30)
        assert entered.wait(5)
        try:
            store.append_event("one", event("user_message", "added", content="new"))
        finally:
            release.set()
        assert future.result(5).active_events[-1].uuid == "added"
    assert calls == 1
    monkeypatch.setattr(store, "_read_records", forbid)
    assert store.query_page("one", turn_limit=30).active_events[-1].uuid == "added"


def test_delete_removes_derived_bodies(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.query_page("one", turn_limit=30)
    path = store._business_index.path("one")
    assert path.exists()
    store.delete("one")
    assert not path.exists()


def test_index_maintenance_failure_does_not_fail_durable_append_and_can_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.query_page("one", turn_limit=30)
    with monkeypatch.context() as patch:
        patch.setattr(
            store._business_index,
            "advance",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )
        store.append_event("one", event("user_message", "saved", content="once"))
    # A valid warm in-memory snapshot repairs a missed disk-index update.
    assert store.query_page("one", turn_limit=30).active_events[-1].uuid == "saved"
    assert sum(e.uuid == "saved" for e in store.load("one").events) == 1


def test_cancelled_build_is_not_published_and_other_sessions_remain_available(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.append_events("one", [event("user_message", f"u{i}", content="hi") for i in range(20)])
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls >= 15:
            raise InterruptedError("navigation left")

    with pytest.raises(InterruptedError, match="navigation left"):
        store.query_page("one", turn_limit=30, cancel_check=cancel)
    assert not store._business_index.matches("one", store._source_version(tmp_path / "one.jsonl"))
    assert len(store.query_page("one", turn_limit=30).active_events) == 21


def test_new_process_reads_persistent_page_without_raw_or_full_business_loading(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.append_event("one", event("user_message", "u", content="hello"))
    store.query_page("one", turn_limit=30)
    program = """
import sys
from agent import JsonlSessionStore
store = JsonlSessionStore(sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError('persistent page must not scan or hydrate full history')
store._read_records = forbidden
store.query = forbidden
assert store.query_page('one', turn_limit=30).active_events[-1].uuid == 'u'
"""
    # This checks cross-process index reuse, not interpreter startup performance.
    # Importing agent also loads provider dependencies; allow startup headroom
    # on loaded Windows runners while keeping the child bounded.
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_one_shot_io_shaped_cancel_is_not_reclassified_as_cache_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.query_page("one", turn_limit=30)
    fresh = JsonlSessionStore(tmp_path)
    original = fresh._business_index.read
    reached = False
    cancelled = False

    def entering(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal reached
        reached = True
        return original(*args, **kwargs)  # type: ignore[arg-type]

    def cancel_once() -> None:
        nonlocal cancelled
        if reached and not cancelled:
            cancelled = True
            raise InterruptedError("one cancellation")

    monkeypatch.setattr(fresh._business_index, "read", entering)
    with pytest.raises(InterruptedError, match="one cancellation"):
        fresh.query("one", cancel_check=cancel_once)
    assert fresh.query_page("one", turn_limit=30).active_events == (
        store.query("one").active_events[0],
    )


@pytest.mark.parametrize("turn_limit", [1, 2, 3, 30])
def test_native_pages_equal_legacy_complete_turn_projection(
    tmp_path: Path, turn_limit: int
) -> None:
    store = make_store(tmp_path)
    events = [event("run_started", "started0", run_id="run", root_run_id="root")]
    for index in range(7):
        events += [
            event(
                "system_trigger" if index == 3 else "user_message",
                f"u{index}",
                content=f"input {index}",
                presentation="none" if index == 3 else "conversation",
            ),
            event(
                "tool_call_request",
                f"call{index}",
                tool_call_id=f"call{index}",
                tool_name="exec_command",
                arguments_json='{"cmd":"echo test"}',
            ),
            event(
                "tool_call_result",
                f"result{index}",
                tool_call_id=f"call{index}",
                content="结果😀" * 12000,
            ),
            event("assistant_message", f"a{index}", content=f"answer {index}"),
            event("run_terminal", f"terminal{index}", run_id="run", status="interrupted"),
        ]
    store.append_events("one", events)
    expected = store.query("one")
    fresh = JsonlSessionStore(tmp_path)
    before = None
    collected: list[Event] = []
    while True:
        want, cursor = expected.page_events(turn_limit=turn_limit, before=before)
        page = fresh.query_page(
            "one", turn_limit=turn_limit, before=before, history_epoch=expected.history_epoch
        )
        assert page.active_events == want
        assert page.history_cursor == cursor
        assert page.recovery is not None and page.recovery.source_run_id == "run"
        collected[:0] = page.active_events
        if cursor is None:
            break
        before = cursor
    assert tuple(collected) == expected.active_events


def test_persistent_page_only_decodes_requested_body_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.append_events(
        "one",
        [
            item
            for i in range(60)
            for item in (
                event("user_message", f"u{i}", content=f"body-marker-{i}"),
                event("assistant_message", f"a{i}", content=f"body-marker-{i}" + "x" * 20000),
            )
        ],
    )
    store.query_page("one", turn_limit=2)
    fresh = JsonlSessionStore(tmp_path)
    monkeypatch.setattr(fresh, "query", forbid)
    monkeypatch.setattr(fresh, "_read_records", forbid)
    original = Event.from_jsonl
    decoded = []

    def decode(raw: str) -> Event:
        result = original(raw)
        if "content" in result.payload:
            decoded.append(result.uuid)
        return result

    monkeypatch.setattr(Event, "from_jsonl", decode)
    page = fresh.query_page("one", turn_limit=2)
    assert decoded == ["u58", "a58", "u59", "a59"]
    decoded.clear()
    older = fresh.query_page(
        "one", turn_limit=2, before=page.history_cursor, history_epoch=page.history_epoch
    )
    assert decoded == ["u56", "a56", "u57", "a57"]
    assert len(older.active_events[-1].payload["content"]) > 20000


def test_append_retains_epoch_rewrite_invalidates_it_and_removed_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.append_events("one", [event("user_message", f"u{i}", content="hi") for i in range(4)])
    first = store.query_page("one", turn_limit=2)
    monkeypatch.setattr(store, "_read_records", forbid)
    store.append_event("one", event("assistant_message", "a", content="yes"))
    assert (
        store.query_page("one", turn_limit=2, history_epoch=first.history_epoch).history_epoch
        == first.history_epoch
    )
    store.append_event(
        "one", event("turn_rewrite", "rewrite", inactive_event_uuids=["u2", "u3", "a"])
    )
    with pytest.raises(ValueError, match="changed"):
        store.query_page(
            "one", turn_limit=2, before=first.history_cursor, history_epoch=first.history_epoch
        )
    with pytest.raises(ValueError, match="no longer available"):
        store.query_page("one", turn_limit=2, before="u2")
    latest = store.query_page("one", turn_limit=2)
    assert [e.uuid for e in latest.active_events][1:] == ["u0", "u1"]
    with pytest.raises(ValueError, match="complete turn boundary"):
        store.query_page("one", turn_limit=2, before="u0")


def test_recovery_and_current_metadata_stay_current_on_older_page(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_events("one", [event("user_message", f"u{i}", content="hi") for i in range(3)])
    store.append_events(
        "one",
        [
            event("run_started", "start", run_id="run", root_run_id="root"),
            event("tool_call_request", "tool", tool_call_id="tool"),
            event("run_terminal", "stop", run_id="run", status="interrupted"),
            event("persona_change", "persona", to="new-persona"),
        ],
    )
    latest = store.query_page("one", turn_limit=1)
    assert latest.recovery is None
    store.append_event(
        "one", event("tool_call_result", "result", tool_call_id="tool", content="ok")
    )
    older = store.query_page("one", turn_limit=1, before=latest.history_cursor)
    assert older.recovery is not None and older.recovery.root_run_id == "root"
    assert older.current_persona == store.query("one").current_persona == "new-persona"
    assert [e.uuid for e in older.active_events] == ["u1"]
    store.append_event("one", event("system_trigger", "silent", presentation="none"))
    assert store.query_page("one", turn_limit=1).recovery is None


def test_old_index_schema_rebuilds_without_changing_original(tmp_path: Path) -> None:
    import sqlite3

    store = make_store(tmp_path)
    store.append_event("one", event("user_message", "u", content="hello"))
    expected = store.query_page("one", turn_limit=1).active_events
    source = (tmp_path / "one.jsonl").read_bytes()
    with closing(sqlite3.connect(store._business_index.path("one"))) as db, db:
        db.execute("PRAGMA user_version=3")
        db.execute("CREATE TABLE runs (id TEXT)")
    fresh = JsonlSessionStore(tmp_path)
    assert fresh.query_page("one", turn_limit=1).active_events == expected
    with closing(sqlite3.connect(fresh._business_index.path("one"))) as db, db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='runs'").fetchone()
        columns = {r[1] for r in db.execute("PRAGMA table_info(events)")}
        assert columns == {"position", "uuid", "kind", "body", "active", "recovery"}
    assert (tmp_path / "one.jsonl").read_bytes() == source


def test_source_change_during_page_read_is_not_published_as_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.query_page("one", turn_limit=1)
    original = store._business_index.page

    def racing(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        store.append_event("one", event("user_message", "new", content="changed"))
        return result

    monkeypatch.setattr(store._business_index, "page", racing)
    with pytest.raises(ValueError, match="changed"):
        store.query_page("one", turn_limit=1)


def test_corrupted_index_body_rebuilds_from_original(tmp_path: Path) -> None:
    import sqlite3

    store = make_store(tmp_path)
    store.append_event("one", event("user_message", "u", content="intact"))
    expected = store.query_page("one", turn_limit=1).active_events
    source = (tmp_path / "one.jsonl").read_bytes()
    with closing(sqlite3.connect(store._business_index.path("one"))) as db, db:
        db.execute("UPDATE events SET body='not-json' WHERE uuid='u'")
    assert JsonlSessionStore(tmp_path).query_page("one", turn_limit=1).active_events == expected
    assert (tmp_path / "one.jsonl").read_bytes() == source
