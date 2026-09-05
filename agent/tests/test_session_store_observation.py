"""Lock observations remain content-free and do not alter persistence outcomes."""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import cast

import portalocker
import pytest
from agent.sessions import file_observation
from agent.sessions.file_observation import observe_session_file
from portalocker.exceptions import LockException

from agent import JsonlSessionStore, Session, SessionCorruptError, SessionPersistError


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == file_observation.__name__]


def test_create_append_load_observe_sizes_and_redact_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=file_observation.__name__)
    store = JsonlSessionStore(tmp_path)
    session = Session.new("private title", "default", "test/model", session_id="private identity")
    store.create(session)
    created_bytes = (tmp_path / "private identity.jsonl").stat().st_size
    # The observation must not serialize any field of this event into logging.
    event = session.events[0]
    store.append_event(session.session_id, event)
    store.load(session.session_id)

    records = _records(caplog)
    assert [record.__dict__["session_file_operation"] for record in records] == [
        "create",
        "append",
        "load",
    ]
    assert [record.__dict__["byte_count"] for record in records] == [
        created_bytes,
        created_bytes,
        created_bytes * 2,
    ]
    assert all(record.__dict__["outcome"] == "success" for record in records)
    assert all(record.__dict__["lock_acquired"] is True for record in records)
    assert all(
        record.__dict__["process_id"] > 0 and record.__dict__["thread_id"] > 0 for record in records
    )
    assert records[-1].__dict__["parse_ms"] is not None
    assert records[0].__dict__["parse_ms"] is None
    expected_key = hashlib.sha256(session.session_id.encode()).hexdigest()[:16]
    assert all(record.__dict__["session_key"] == expected_key for record in records)
    assert "private title" not in caplog.text
    assert "private identity" not in caplog.text
    assert "test/model" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_real_file_lock_is_released_before_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("title", "default", "test/model", session_id="session-a")
    path = tmp_path / "session-a.jsonl"
    operations: list[str] = []

    def log_after_release(level: int, message: str, *args: object, **kwargs: object) -> None:
        # A zero-wait exclusive lock would fail if the original create, append,
        # or load still held its lock. Do not let emit() swallow the assertion:
        # record success only after the lock is actually acquired.
        with portalocker.Lock(path, "a+b", timeout=0):
            operations.append(str(args[0]))

    monkeypatch.setattr(file_observation.logger, "log", log_after_release)
    store.create(session)
    store.append_event(session.session_id, session.events[0])
    store.load(session.session_id)
    assert operations == ["create", "append", "load"]


@pytest.mark.parametrize("operation", ["create", "append", "load"])
def test_lock_timeout_reports_wait_without_claiming_a_held_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("private", "default", "test/model", session_id="session-a")
    if operation != "create":
        store.create(session)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger=file_observation.__name__)
    failure = LockException("SECRET_LOCK_DETAIL")
    clock = [0.0]

    @contextmanager
    def timeout_lock(*args: object, **kwargs: object) -> Iterator[object]:
        clock[0] = 10.0
        raise failure
        yield  # pragma: no cover

    monkeypatch.setattr(portalocker, "Lock", timeout_lock)
    monkeypatch.setattr(file_observation, "perf_counter", lambda: clock[0])
    with pytest.raises(SessionPersistError, match="锁超时") as caught:
        if operation == "create":
            store.create(session)
        elif operation == "append":
            store.append_event(session.session_id, session.events[0])
        else:
            store.load(session.session_id)
    assert caught.value.__cause__ is failure
    record = _records(caplog)[0]
    assert record.levelno == logging.WARNING
    assert record.__dict__["outcome"] == "lock_timeout"
    assert record.__dict__["lock_acquired"] is False
    assert record.__dict__["wait_ms"] == 10_000
    assert record.__dict__["hold_ms"] is None
    assert record.__dict__["parse_ms"] is None
    assert "SECRET_LOCK_DETAIL" not in caplog.text


@pytest.mark.parametrize("operation", ["create", "append", "load"])
def test_io_failure_is_observed_after_release_and_preserves_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("private", "default", "test/model", session_id="session-a")
    if operation != "create":
        store.create(session)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger=file_observation.__name__)
    failure = OSError("SECRET_IO_DETAIL")
    released = False

    class FailingFile(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise failure

        def write(self, buffer: object) -> int:
            raise failure

    @contextmanager
    def failing_lock(*args: object, **kwargs: object) -> Iterator[FailingFile]:
        nonlocal released
        try:
            yield FailingFile()
        finally:
            released = True

    monkeypatch.setattr(portalocker, "Lock", failing_lock)
    with pytest.raises(SessionPersistError) as caught:
        if operation == "create":
            store.create(session)
        elif operation == "append":
            store.append_event(session.session_id, session.events[0])
        else:
            store.load(session.session_id)
    assert caught.value.__cause__ is failure
    assert released
    record = _records(caplog)[0]
    assert record.levelno == logging.WARNING
    assert record.__dict__["outcome"] == "error"
    assert record.__dict__["lock_acquired"] is True
    assert record.__dict__["hold_ms"] is not None
    assert record.__dict__["parse_ms"] is None
    assert "SECRET_IO_DETAIL" not in caplog.text


def test_parse_failure_has_separate_timing_and_no_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = JsonlSessionStore(tmp_path)
    (tmp_path / "bad.jsonl").write_text("SECRET_BROKEN_HISTORY", encoding="utf-8")
    caplog.set_level(logging.DEBUG, logger=file_observation.__name__)
    with pytest.raises(SessionCorruptError):
        store.load("bad")
    record = _records(caplog)[0]
    assert record.levelno == logging.WARNING
    assert record.__dict__["outcome"] == "error"
    assert record.__dict__["lock_acquired"] is True
    assert record.__dict__["parse_ms"] is not None
    assert record.__dict__["error_type"] == "SessionCorruptError"
    assert "SECRET_BROKEN_HISTORY" not in caplog.text


@pytest.mark.parametrize(
    "wait_seconds, hold_seconds, expected_level",
    [(0.01, 0.02, logging.DEBUG), (0.01, 0.25, logging.INFO), (0.25, 0.02, logging.INFO)],
)
def test_parse_time_is_excluded_from_lock_time_and_log_level(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    wait_seconds: float,
    hold_seconds: float,
    expected_level: int,
) -> None:
    clock = [0.0]
    caplog.set_level(logging.DEBUG, logger=file_observation.__name__)
    monkeypatch.setattr(file_observation, "perf_counter", lambda: clock[0])

    class TimedLock:
        def __enter__(self) -> object:
            clock[0] = wait_seconds
            return object()

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            clock[0] = wait_seconds + hold_seconds

    with observe_session_file("load", "session-a") as observation:
        with observation.locked(cast(portalocker.Lock, TimedLock())):
            pass
        with observation.parsing():
            clock[0] += 20.0
    record = _records(caplog)[0]
    assert record.levelno == expected_level
    assert record.__dict__["wait_ms"] == wait_seconds * 1000
    assert record.__dict__["hold_ms"] == hold_seconds * 1000
    assert record.__dict__["parse_ms"] == 20_000.0


@pytest.mark.parametrize("failure_stage", ["logging", "identity"])
def test_logging_failure_cannot_change_storage_success_or_mask_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("private", "default", "test/model", session_id="session-a")

    def logging_failed(*args: object, **kwargs: object) -> None:
        raise RuntimeError("logging unavailable")

    if failure_stage == "logging":
        monkeypatch.setattr(file_observation.logger, "log", logging_failed)
    else:
        monkeypatch.setattr(hashlib, "sha256", logging_failed)
    store.create(session)
    assert store.load(session.session_id).session_id == session.session_id
    (tmp_path / "bad.jsonl").write_text("malformed", encoding="utf-8")
    with pytest.raises(SessionCorruptError):
        store.load("bad")
