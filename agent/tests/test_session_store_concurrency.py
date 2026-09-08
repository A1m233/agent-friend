"""Session readers parse immutable snapshots without retaining the file lock."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, current_thread
from threading import Event as ThreadEvent
from typing import BinaryIO

import pytest
from agent.sessions.query import CancelCheck

from agent import Event, JsonlSessionStore, Session


def _event(event_id: str, text: str) -> Event:
    return Event(
        type="user_message",
        uuid=event_id,
        ts=datetime.now(UTC),
        payload={"content": text},
    )


def test_writer_commits_before_snapshot_bytes_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("synthetic", "default", "test/model")
    store.create(session)
    ready, release = ThreadEvent(), ThreadEvent()
    read = store._read_snapshot_bytes

    def paused_read(binary: BinaryIO, length: int, cancel_check: CancelCheck) -> list[bytes]:
        if current_thread().name.startswith("pinned-reader"):
            ready.set()
            assert release.wait(timeout=5)
        return read(binary, length, cancel_check)

    monkeypatch.setattr(store, "_read_snapshot_bytes", paused_read)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pinned-reader") as pool:
        reader = pool.submit(store.load, session.session_id)
        try:
            assert ready.wait(timeout=5)
            store.append_event(session.session_id, _event("later", "committed after snapshot"))
        finally:
            release.set()
        old = reader.result(timeout=5)
    assert len(old.events) == 1
    assert not store.can_reuse_loaded(old)
    assert store.load(session.session_id).events[-1].uuid == "later"


def test_readers_and_other_appends_progress_while_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("synthetic", "default", "test/model")
    store.create(session)
    encoding = ThreadEvent()
    release = ThreadEvent()
    original = Event.to_jsonl

    def encode(event: Event) -> str:
        if event.uuid == "slow":
            encoding.set()
            assert release.wait(timeout=5)
        return original(event)

    monkeypatch.setattr(Event, "to_jsonl", encode)
    with ThreadPoolExecutor(max_workers=2) as executor:
        slow = executor.submit(
            store.append_event, session.session_id, _event("slow", "large payload")
        )
        try:
            assert encoding.wait(timeout=5)
            # Both operations must finish before encoding can finish, not just
            # eventually succeed after a lock timeout or retry.
            reader = executor.submit(store.load, session.session_id)
            assert len(reader.result(timeout=2).events) == 1
            writer = executor.submit(
                store.append_event, session.session_id, _event("fast", "result")
            )
            writer.result(timeout=2)
        finally:
            release.set()
        slow.result(timeout=5)
    assert [e.uuid for e in store.load(session.session_id).events[1:]] == ["fast", "slow"]


@pytest.mark.parametrize("reader_count", [1, 4])
def test_writers_progress_while_readers_parse_their_original_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader_count: int
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("synthetic", "default", "test/model", session_id="session-a")
    store.create(session)
    original_events = [
        _event(f"original-{index}", "synthetic history " * 1000) for index in range(96)
    ]
    store.append_events(session.session_id, original_events)
    parsing_started = Barrier(reader_count + 1)
    release_parsing = ThreadEvent()
    original_parse = Event.from_jsonl

    def parse_with_barrier(line: str) -> Event:
        if current_thread().name.startswith("snapshot-reader") and '"session_meta"' in line:
            parsing_started.wait(timeout=5)
            if not release_parsing.wait(timeout=5):
                raise AssertionError("writer could not finish while readers were parsing")
        return original_parse(line)

    monkeypatch.setattr(Event, "from_jsonl", parse_with_barrier)
    additions = [_event("new-first", "new batch first"), _event("new-second", "new batch second")]
    with (
        ThreadPoolExecutor(
            max_workers=reader_count, thread_name_prefix="snapshot-reader"
        ) as readers,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-writer") as writers,
    ):
        read_futures = [readers.submit(store.load, session.session_id) for _ in range(reader_count)]
        try:
            parsing_started.wait(timeout=5)
            write_future = writers.submit(store.append_events, session.session_id, additions)
            # Synchronization, not elapsed-time comparison, proves the writer
            # finishes before any reader is permitted to finish parsing.
            write_future.result(timeout=5)
        finally:
            release_parsing.set()
        snapshots = [future.result(timeout=5) for future in read_futures]

    old_ids = [session.events[0].uuid, *(event.uuid for event in original_events)]
    assert all([event.uuid for event in snapshot.events] == old_ids for snapshot in snapshots)
    fresh = store.load(session.session_id)
    assert [event.uuid for event in fresh.events] == [*old_ids, "new-first", "new-second"]
