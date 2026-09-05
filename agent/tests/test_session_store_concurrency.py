"""Session readers parse immutable snapshots without retaining the file lock."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, current_thread
from threading import Event as ThreadEvent

import pytest

from agent import Event, JsonlSessionStore, Session


def _event(event_id: str, text: str) -> Event:
    return Event(
        type="user_message",
        uuid=event_id,
        ts=datetime.now(UTC),
        payload={"content": text},
    )


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
