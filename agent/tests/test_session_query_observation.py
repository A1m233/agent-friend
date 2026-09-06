"""Query telemetry explains reuse without exposing content or changing outcomes."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Event as ThreadEvent
from threading import current_thread
from typing import Any

import pytest
from agent.runtime.cancellation import CancellationToken, CancelReason, RunCancelled
from agent.sessions import query_observation
from agent.sessions.query import CancelCheck, SessionQuerySnapshot
from agent.sessions.query_observation import SessionQueryObservation

from agent import Event, JsonlSessionStore, Session, SessionCorruptError


def _store(tmp_path: Path) -> tuple[JsonlSessionStore, str]:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("PRIVATE_TITLE", "default", "test/model", session_id="PRIVATE_SESSION_ID")
    store.create(session)
    return store, session.session_id


def _message(uuid: str) -> Event:
    return Event(
        type="user_message",
        uuid=uuid,
        ts=datetime.now(UTC),
        payload={"content": "PRIVATE_CONTENT", "credential": "PRIVATE_CREDENTIAL"},
    )


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == query_observation.__name__]


def test_query_logs_cold_hit_and_external_invalidation_without_counting_file_reads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=query_observation.__name__)
    store, sid = _store(tmp_path)
    first = store.query(sid)
    assert store.query(sid) is first
    JsonlSessionStore(tmp_path).append_event(sid, _message("new"))
    assert store.query(sid).active_events[-1].uuid == "new"

    records = _records(caplog)
    assert [record.cache_action for record in records] == ["rebuilt", "hit", "rebuilt"]  # type: ignore[attr-defined]
    assert [record.cache_miss_reason for record in records] == ["absent", None, "source_changed"]  # type: ignore[attr-defined]
    assert [record.cache_published for record in records] == [True, None, True]  # type: ignore[attr-defined]
    for record in records:
        assert "session_file_operation" not in record.__dict__
        assert record.__dict__["process_id"] > 0
        assert record.__dict__["thread_id"] > 0
        assert record.__dict__["query_total_ms"] >= record.__dict__["query_wait_ms"] >= 0
    assert len({record.__dict__["session_key"] for record in records}) == 1
    for secret in (sid, str(tmp_path), "PRIVATE_TITLE", "PRIVATE_CONTENT", "PRIVATE_CREDENTIAL"):
        assert secret not in caplog.text


def test_coalesced_query_is_distinguished_from_a_second_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=query_observation.__name__)
    store, sid = _store(tmp_path)
    parsing, release, waiter_entered = ThreadEvent(), ThreadEvent(), ThreadEvent()
    original_parse = Event.from_jsonl
    original_building = store._query_cache.building

    def block_parse(line: str) -> Event:
        parsing.set()
        assert release.wait(5)
        return original_parse(line)

    @contextmanager
    def mark_waiter(
        session_id: str,
        cancel_check: CancelCheck,
        *,
        observation: SessionQueryObservation | None = None,
    ) -> Iterator[None]:
        if current_thread().name.startswith("query-waiter"):
            waiter_entered.set()
        with original_building(session_id, cancel_check, observation=observation):
            yield

    monkeypatch.setattr(Event, "from_jsonl", block_parse)
    monkeypatch.setattr(store._query_cache, "building", mark_waiter)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="query-builder") as builder,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="query-waiter") as waiter,
    ):
        first = builder.submit(store.query, sid)
        assert parsing.wait(5)
        second = waiter.submit(store.query, sid)
        try:
            assert waiter_entered.wait(5)
        finally:
            release.set()
        assert first.result(timeout=5) is second.result(timeout=5)
    assert sorted(record.__dict__["cache_action"] for record in _records(caplog)) == [
        "coalesced",
        "rebuilt",
    ]
    coalesced = next(
        record for record in _records(caplog) if record.__dict__["cache_action"] == "coalesced"
    )
    assert coalesced.__dict__["cache_published"] is None
    assert coalesced.__dict__["query_wait_ms"] >= 0


def test_cancel_and_corruption_have_distinct_content_free_outcomes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=query_observation.__name__)
    store, sid = _store(tmp_path)
    token = CancellationToken()
    token.cancel(CancelReason.EXPLICIT_REQUEST)
    with pytest.raises(RunCancelled):
        store.query(sid, cancel_check=token.raise_if_cancelled)
    (tmp_path / f"{sid}.jsonl").write_text("PRIVATE_CORRUPT_CONTENT", encoding="utf-8")
    with pytest.raises(SessionCorruptError):
        store.query(sid)
    records = _records(caplog)
    assert [record.__dict__["outcome"] for record in records] == ["cancelled", "error"]
    assert [record.__dict__["error_type"] for record in records] == [
        "RunCancelled",
        "SessionCorruptError",
    ]
    assert records[0].levelno == logging.INFO
    assert records[1].levelno == logging.WARNING
    assert "PRIVATE_CORRUPT_CONTENT" not in caplog.text


def test_capacity_and_source_race_explain_why_a_build_was_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=query_observation.__name__)
    store, sid = _store(tmp_path)
    JsonlSessionStore(tmp_path, query_cache_bytes=0).query(sid)
    build = SessionQuerySnapshot.build

    def append_during_projection(*args: Any, **kwargs: Any) -> SessionQuerySnapshot:
        snapshot = build(*args, **kwargs)
        JsonlSessionStore(tmp_path).append_event(sid, _message("racing"))
        return snapshot

    monkeypatch.setattr(SessionQuerySnapshot, "build", append_during_projection)
    assert store.query(sid).raw_event_count == 1
    records = _records(caplog)
    assert [record.__dict__["cache_published"] for record in records] == [False, False]
    assert [record.__dict__["cache_publish_reason"] for record in records] == [
        "capacity",
        "source_changed",
    ]


def test_maintenance_failure_log_does_not_change_durable_write_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=query_observation.__name__)
    store, sid = _store(tmp_path)
    store.query(sid)
    caplog.clear()

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("PRIVATE_EXCEPTION_DETAIL")

    monkeypatch.setattr(store, "_advance_read_caches", fail)
    store.append_event(sid, _message("durable"))
    assert store.load(sid).events[-1].uuid == "durable"
    record = _records(caplog)[0]
    assert record.__dict__["cache_action"] == "invalidated"
    assert record.__dict__["cache_miss_reason"] == "maintenance_failed"
    assert record.__dict__["error_type"] == "OSError"
    assert record.__dict__["cache_published"] is False
    assert "PRIVATE_EXCEPTION_DETAIL" not in caplog.text


def test_query_logging_failure_preserves_success_and_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)

    def fail_log(*args: object, **kwargs: object) -> None:
        raise RuntimeError("broken handler")

    monkeypatch.setattr(query_observation.logger, "log", fail_log)
    snapshot = store.query(sid)
    assert store.query(sid) is snapshot
    token = CancellationToken()
    token.cancel(CancelReason.EXPLICIT_REQUEST)
    with pytest.raises(RunCancelled):
        store.query(sid, cancel_check=token.raise_if_cancelled)


def test_query_logs_only_after_releasing_cache_and_builder_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid = _store(tmp_path)
    verified: list[bool] = []

    def log(*args: object, **kwargs: object) -> None:
        cache = store._query_cache
        if not cache._lock.acquire(blocking=False):
            return
        try:
            verified.append(sid not in cache._builders)
        finally:
            cache._lock.release()

    monkeypatch.setattr(query_observation.logger, "log", log)
    store.query(sid)
    store.query(sid)
    assert verified == [True, True]


def test_exception_classification_cannot_replace_the_original_exception() -> None:
    class UninspectableError(Exception):
        @property
        def cancellation(self) -> object:
            raise RuntimeError("cannot inspect this exception")

    original = UninspectableError("PRIVATE_EXCEPTION_DETAIL")
    with (
        pytest.raises(UninspectableError) as caught,
        query_observation.observe_session_query("PRIVATE_SESSION_ID"),
    ):
        raise original
    assert caught.value is original
