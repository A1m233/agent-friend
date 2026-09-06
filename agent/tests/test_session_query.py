from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event as ThreadEvent

import pytest
from agent.sessions import list_session_activity, query_session
from agent.sessions.query import SessionQuerySnapshot

from agent import Event, JsonlSessionStore, Session, SessionCorruptError


class _QueryCancelled(Exception):
    pass


def _event(kind: str, uuid: str, **payload: object) -> Event:
    return Event(type=kind, uuid=uuid, ts=datetime.now(UTC), payload=payload)  # type: ignore[arg-type]


def _store(tmp_path: Path) -> tuple[JsonlSessionStore, str]:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("Query", "default", "test/model", session_id="one")
    store.create(session)
    return store, session.session_id


def test_query_reuses_frozen_business_projection_and_raw_event_positions(tmp_path: Path) -> None:
    store, sid = _store(tmp_path)
    store.append_events(
        sid,
        [
            _event("user_message", "u1", content="hello", nested={"values": [1]}),
            _event("model_transcript_item", "internal", message={"content": "x" * 100_000}),
            _event("assistant_message", "a1", content="answer"),
        ],
    )
    first = query_session(store, sid)
    assert query_session(store, sid) is first
    assert [event.uuid for event in first.events] == [sid, "u1", "a1"]
    assert first.raw_event_count == 4
    assert first.raw_event_indices["a1"] == 3
    assert first.estimated_bytes < 10_000
    with pytest.raises(TypeError):
        first.events[1].payload["content"] = "changed"
    with pytest.raises(TypeError):
        first.events[1].payload["nested"]["values"].append(2)
    assert len(store.load(sid).events) == 4


def test_known_append_and_rewrite_do_not_reparse_old_rollout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)
    store.append_event(sid, _event("user_message", "old", content="old branch"))
    before = query_session(store, sid)

    def unexpected_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("known append should not read the existing JSONL")

    monkeypatch.setattr(store, "_read_records", unexpected_read)
    store.append_events(
        sid,
        [
            _event("rollout_commit", "commit"),
            _event("turn_rewrite", "rewrite", inactive_event_uuids=["old"]),
            _event("user_message", "new", content="new branch"),
        ],
    )
    after = query_session(store, sid)
    assert after.source_version != before.source_version
    assert [event.uuid for event in after.active_events] == [sid, "new"]
    assert after.raw_event_indices["new"] == 4
    assert [event.uuid for event in before.active_events] == [sid, "old"]


def test_external_append_replace_and_truncate_invalidate_without_broker(tmp_path: Path) -> None:
    store, sid = _store(tmp_path)
    initial = query_session(store, sid)
    external = JsonlSessionStore(tmp_path)
    external.append_event(sid, _event("user_message", "external", content="external"))
    appended = query_session(store, sid)
    assert appended is not initial
    assert appended.active_events[-1].uuid == "external"
    path = tmp_path / f"{sid}.jsonl"
    replacement = tmp_path / "replacement.tmp"
    replacement.write_bytes(path.read_bytes().replace(b"external", b"modified"))
    replacement.replace(path)
    assert query_session(store, sid).active_events[-1].payload["content"] == "modified"
    path.write_text(initial.events[0].to_jsonl() + "\n", encoding="utf-8")
    assert query_session(store, sid).raw_event_count == 1


def test_activity_prefilters_targets_and_exclusions_before_reverse_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)
    other = Session.new("Other", "default", "test/model", session_id="other")
    store.create(other)
    store.append_event(sid, _event("user_message", "u", content="large"))
    read = store._last_conversation_event_at
    scanned: list[str] = []

    def observe(path: Path, **kwargs: object) -> datetime:
        scanned.append(path.stem)
        return read(path, **kwargs)  # type: ignore[arg-type]

    def forbidden_model(*args: object, **kwargs: object) -> None:
        raise AssertionError("history enumeration must not resolve a model")

    monkeypatch.setattr(store, "_last_conversation_event_at", observe)
    monkeypatch.setattr(store, "_latest_model_runtime_config", forbidden_model)
    assert [
        item.session_id for item in list_session_activity(store, exclude_session_ids=[sid])
    ] == ["other"]
    assert scanned == ["other"]
    assert [item.session_id for item in list_session_activity(store, session_ids=[sid])] == [sid]
    assert scanned == ["other", sid]
    assert list_session_activity(store, session_ids=[]) == ()


def test_summary_reuse_tracks_raw_activity_and_model_after_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)
    original = store.list()[0]

    def unexpected_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("unchanged or known appended summaries should reuse metadata")

    monkeypatch.setattr(store, "_read_uncached_summary", unexpected_read)
    assert store.list()[0] == original
    event = Event(
        type="user_message",
        uuid="u",
        ts=original.updated_at + timedelta(seconds=1),
        payload={"content": "next"},
    )
    store.append_events(sid, [event, _event("turn_rewrite", "r", inactive_event_uuids=["u"])])
    assert store.list()[0].updated_at == event.ts
    assert all(item.uuid != "u" for item in query_session(store, sid).active_events)


def test_query_preserves_corrupt_tail_policy_and_never_publishes_cancelled_build(
    tmp_path: Path,
) -> None:
    store, sid = _store(tmp_path)
    path = tmp_path / f"{sid}.jsonl"
    with path.open("ab") as handle:
        handle.write(b"broken\n")
    with pytest.raises(SessionCorruptError):
        query_session(store, sid)
    # A committed prefix permits exactly the same recoverable-tail diagnosis as load.
    path.write_text(
        Session.new("Query", "default", "test/model", session_id=sid).events[0].to_jsonl() + "\n",
        encoding="utf-8",
    )
    store.append_event(sid, _event("rollout_commit", "c"))
    with path.open("ab") as handle:
        handle.write(b"broken\n")
    assert query_session(store, sid).load_diagnostics == store.load(sid).load_diagnostics
    fresh = JsonlSessionStore(tmp_path)
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls == 6:
            raise _QueryCancelled("cancelled")

    with pytest.raises(_QueryCancelled):
        query_session(fresh, sid, cancel_check=cancel)
    assert query_session(fresh, sid).load_diagnostics.unhandled_tail_corruption


def test_builders_are_per_session_and_append_can_progress_during_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)
    other = Session.new("Other", "default", "test/model", session_id="other")
    store.create(other)
    entered, release = ThreadEvent(), ThreadEvent()
    original = Event.from_jsonl
    blocked_once = False

    def blocked(line: str) -> Event:
        nonlocal blocked_once
        if not blocked_once and '"one"' in line:
            blocked_once = True
            entered.set()
            assert release.wait(5)
        return original(line)

    monkeypatch.setattr(Event, "from_jsonl", blocked)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(query_session, store, sid)
        assert entered.wait(5)
        try:
            assert (
                pool.submit(query_session, store, "other").result(timeout=2).session_id == "other"
            )
            pool.submit(
                store.append_event, sid, _event("user_message", "new", content="new")
            ).result(timeout=2)
        finally:
            release.set()
        old = first.result(timeout=5)
    assert old.raw_event_count == 1
    assert query_session(store, sid).raw_event_count == 2


def test_cache_capacity_and_deleted_files_do_not_serve_stale_values(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path, query_cache_entries=1, query_cache_bytes=2048)
    for sid in ("first", "second"):
        store.create(Session.new("Query", "default", "test/model", session_id=sid))
    first: SessionQuerySnapshot = query_session(store, "first")
    query_session(store, "second")
    assert query_session(store, "first") is not first
    store.delete("first")
    assert list_session_activity(store, session_ids=["first"]) == ()


def test_cache_failure_cannot_turn_a_durable_append_into_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)
    query_session(store, sid)

    def fail_cache(*args: object, **kwargs: object) -> None:
        raise OSError("projection became unavailable after fsync")

    with monkeypatch.context() as patch:
        patch.setattr(store._query_cache, "put", fail_cache)
        store.append_event(sid, _event("user_message", "once", content="durable"))
    assert [event.uuid for event in store.load(sid).events].count("once") == 1
    assert query_session(store, sid).active_events[-1].uuid == "once"


@pytest.mark.parametrize("reader", ["list", "list_activity"])
def test_list_skips_a_file_deleted_after_directory_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    store, sid = _store(tmp_path)
    store.create(Session.new("Other", "default", "test/model", session_id="other"))
    version = store._source_version
    removed = False

    def delete_before_stat(path: Path) -> tuple[int, int, int, int, int]:
        nonlocal removed
        if path.stem == sid and not removed:
            removed = True
            path.unlink()
        return version(path)

    monkeypatch.setattr(store, "_source_version", delete_before_stat)
    assert [item.session_id for item in getattr(store, reader)()] == ["other"]


def test_cancelled_cache_hit_does_not_discard_the_valid_shared_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)
    snapshot = query_session(store, sid)
    cancelled = False
    lookup = store._query_cache.get

    def cancel_on_hit(*args: object, **kwargs: object) -> SessionQuerySnapshot | None:
        nonlocal cancelled
        result = lookup(*args, **kwargs)  # type: ignore[arg-type]
        cancelled = True
        return result

    def check() -> None:
        if cancelled:
            raise _QueryCancelled()

    with monkeypatch.context() as patch:
        patch.setattr(store._query_cache, "get", cancel_on_hit)
        with pytest.raises(_QueryCancelled):
            query_session(store, sid, cancel_check=check)
    assert query_session(store, sid) is snapshot


def test_cancelled_waiter_does_not_cancel_another_sessions_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)
    entered, release, cancel_waiter = ThreadEvent(), ThreadEvent(), ThreadEvent()
    original = Event.from_jsonl

    def block(line: str) -> Event:
        entered.set()
        assert release.wait(5)
        return original(line)

    def cancel() -> None:
        if cancel_waiter.is_set():
            raise _QueryCancelled()

    monkeypatch.setattr(Event, "from_jsonl", block)
    with ThreadPoolExecutor(max_workers=2) as pool:
        builder = pool.submit(query_session, store, sid)
        assert entered.wait(5)
        waiter = pool.submit(query_session, store, sid, cancel_check=cancel)
        cancel_waiter.set()
        try:
            with pytest.raises(_QueryCancelled):
                waiter.result(timeout=2)
        finally:
            release.set()
        snapshot = builder.result(timeout=5)
    assert query_session(store, sid) is snapshot
