from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from agent.sessions import Event, Session
from agent.sessions.execution_cache import ExecutionSnapshotCache, SourceDigestTracker
from agent.sessions.rollout import (
    SessionRolloutRecorder,
    finish_rollout_restore,
    is_exact_rollout_candidate,
    restore_rollout,
)
from agent.sessions.store import JsonlSessionStore
from agent.sessions.store_types import SessionLoadDiagnostics, SourceVersion


def test_digest_maintenance_requires_contiguous_clean_durable_bytes() -> None:
    tracker = SourceDigestTracker()
    before: SourceVersion = (1, 2, 3, 4, 5)
    after: SourceVersion = (1, 2, 6, 8, 5)
    tracker.accept("session", before, sha256(b"abc"))
    tracker.advance("session", before, after, [b"de", b"f"], clean_boundary=True)
    assert tracker.digest("session", after) == sha256(b"abcdef").hexdigest()
    assert tracker.digest("session", before) is None
    tracker.advance("session", before, after, [b"def"], clean_boundary=True)
    assert tracker.digest("session", after) is None
    tracker.accept("session", before, sha256(b"abc"))
    tracker.advance("session", before, after, [b"def"], clean_boundary=False)
    assert tracker.digest("session", after) is None
    tracker.accept("session", before, sha256(b"abc"))
    tracker.advance("session", before, after, [b"too many bytes"], clean_boundary=True)
    assert tracker.digest("session", after) is None


def _snapshot(tmp_path: Path) -> tuple[ExecutionSnapshotCache, Session, SourceVersion, bytes]:
    from agent.sessions import SessionCacheEpoch, SessionModelContext, SessionModelHistory

    store = JsonlSessionStore(tmp_path)
    session = Session.new(title="缓存🙂", persona="default", model="deepseek/deepseek-chat")
    store.create(session)
    event = Event(
        type="user_message", uuid="user", ts=session.created_at, payload={"content": "hello"}
    )
    store.append_event(session.session_id, event)
    session.append(event)
    recorder = SessionRolloutRecorder(store=store, session=session)
    recorder.commit(
        history=SessionModelHistory.from_session(session),
        model_context=SessionModelContext(),
        cache_epoch=SessionCacheEpoch(),
        boundary="assistant_completed",
        turn_terminal=True,
    )
    source = (tmp_path / f"{session.session_id}.jsonl").read_bytes()
    version: SourceVersion = (1, 2, len(source), 4, 5)
    tracker = SourceDigestTracker()
    tracker.accept(session.session_id, version, sha256(source))
    cache = ExecutionSnapshotCache(tmp_path, tracker, maintenance_guard=store.maintenance_guard)
    assert cache.publish(
        session, restore_rollout(session).commit, source_version=version, is_current=lambda: True
    )
    return cache, session, version, source


def test_snapshot_rejects_changed_bytes_despite_identical_metadata(tmp_path: Path) -> None:
    cache, session, version, source = _snapshot(tmp_path)
    modified = source.replace(b'"hello"', b'"other"', 1)
    assert len(modified) == len(source) and modified != source
    assert (
        cache.try_load(
            session.session_id,
            source_version=version,
            read_source=lambda: (version, sha256(modified)),
            is_current=lambda: True,
        )
        is None
    )
    observation = cache.observation(session.session_id)
    assert observation is not None and observation.status == "source_changed"


def test_snapshot_source_check_is_cancellable_and_cannot_accept_a_race(tmp_path: Path) -> None:
    cache, session, version, source = _snapshot(tmp_path)

    class Cancelled(Exception):
        pass

    def cancel() -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        cache.try_load(
            session.session_id,
            source_version=version,
            read_source=lambda: (version, sha256(source)),
            is_current=lambda: True,
            cancel_check=cancel,
        )
    assert (
        cache.try_load(
            session.session_id,
            source_version=version,
            read_source=lambda: (version, sha256(source)),
            is_current=lambda: False,
        )
        is None
    )


@pytest.mark.parametrize("mutation", ["payload", "semantics", "schema", "incomplete"])
def test_bad_snapshot_falls_back_before_source_read(tmp_path: Path, mutation: str) -> None:
    cache, session, version, _source = _snapshot(tmp_path)
    path = cache.path(session.session_id)
    envelope = json.loads(path.read_bytes())
    if mutation == "incomplete":
        path.write_bytes(b'{"body":')
    else:
        if mutation == "payload":
            envelope["body"]["state"]["commit"]["messages"][0]["content"] = "wrong"
        else:
            envelope["body"][mutation] += 1
            unsigned = json.dumps(
                envelope["body"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            envelope["sha256"] = sha256(unsigned).hexdigest()
        path.write_text(json.dumps(envelope), encoding="utf-8")

    def no_read() -> tuple[SourceVersion, object]:
        raise AssertionError("invalid snapshot must fall back before hashing")

    assert (
        cache.try_load(
            session.session_id,
            source_version=version,
            read_source=no_read,  # type: ignore[arg-type]
            is_current=lambda: True,
        )
        is None
    )


def test_publish_race_preserves_previous_snapshot_and_removes_temporary(tmp_path: Path) -> None:
    cache, session, version, _source = _snapshot(tmp_path)
    path = cache.path(session.session_id)
    previous = path.read_bytes()
    checks = iter([True, False])
    assert not cache.publish(
        session,
        restore_rollout(session).commit,
        source_version=version,
        is_current=lambda: next(checks),
    )
    assert path.read_bytes() == previous
    assert not list(path.parent.glob("*.tmp"))


def test_pending_tools_never_publish_an_exact_snapshot(tmp_path: Path) -> None:
    cache, session, version, _source = _snapshot(tmp_path)
    session.append(
        Event(
            type="tool_call_request",
            uuid="pending",
            ts=session.created_at,
            payload={"tool_call_id": "call-1", "tool_name": "execute", "args": {}},
        )
    )
    assert not cache.publish(
        session, restore_rollout(session).commit, source_version=version, is_current=lambda: True
    )


def test_orphan_temporary_is_reused_and_cleaned_without_touching_other_sessions(
    tmp_path: Path,
) -> None:
    cache, session, version, _source = _snapshot(tmp_path)
    temporary = cache.path(session.session_id).with_suffix(".tmp")
    other = cache.path("another-session").with_suffix(".tmp")
    other.write_bytes(b"possibly an active publisher")
    for _ in range(3):
        temporary.write_bytes(b"interrupted partial candidate")
        assert cache.publish(
            session,
            restore_rollout(session).commit,
            source_version=version,
            is_current=lambda: True,
        )
        assert not temporary.exists()
        assert other.read_bytes() == b"possibly an active publisher"
    temporary.write_bytes(b"interrupted after final turn")
    with JsonlSessionStore(tmp_path).maintenance_guard(session.session_id):
        cache.delete(session.session_id)
    assert not temporary.exists() and not cache.path(session.session_id).exists()
    assert other.exists()


def test_failed_atomic_replace_preserves_previous_snapshot_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, session, version, _source = _snapshot(tmp_path)
    path = cache.path(session.session_id)
    previous = path.read_bytes()

    def fail_replace(*_args: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr("agent.sessions.execution_cache.os.replace", fail_replace)
    assert not cache.publish(
        session, restore_rollout(session).commit, source_version=version, is_current=lambda: True
    )
    assert path.read_bytes() == previous
    assert not path.with_suffix(".tmp").exists()


def test_snapshot_envelope_keeps_canonical_bytes_and_body_integrity(tmp_path: Path) -> None:
    cache, session, version, source = _snapshot(tmp_path)
    encoded = cache.path(session.session_id).read_bytes()
    envelope = json.loads(encoded)
    canonical = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert encoded == canonical
    body = json.dumps(
        envelope["body"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert envelope["sha256"] == sha256(body).hexdigest()
    loaded = cache.try_load(
        session.session_id,
        source_version=version,
        read_source=lambda: (version, sha256(source)),
        is_current=lambda: True,
    )
    assert loaded is not None and loaded.initial_title == "缓存🙂"
    assert loaded.restored_rollout == restore_rollout(session)


@pytest.mark.parametrize("reason", ["exact", "nonterminal", "redacted", "changed", "tail"])
def test_exact_eligibility_predicate_matches_full_recovery_boundary(
    tmp_path: Path, reason: str
) -> None:
    cache, session, version, _source = _snapshot(tmp_path)
    committed = restore_rollout(session).commit
    assert committed is not None
    if reason == "nonterminal":
        committed = replace(committed, turn_terminal=False)
    elif reason == "redacted":
        committed = replace(
            committed, exact_eligible=False, exact_ineligibility_reason="credential_redacted"
        )
    elif reason == "changed":
        committed = replace(committed, active_event_uuids=("different",))
    elif reason == "tail":
        session.load_diagnostics = SessionLoadDiagnostics(
            parse_error_count=1, unhandled_tail_corruption=True
        )
    exact = reason == "exact"
    assert is_exact_rollout_candidate(session, committed) is exact
    assert finish_rollout_restore(session, committed).exact_continuation_candidate is exact
    assert (
        cache.publish(session, committed, source_version=version, is_current=lambda: True) is exact
    )


def test_cache_cost_observation_is_content_free_and_log_failure_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, session, version, source = _snapshot(tmp_path)

    def broken_log(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("optional observation sink unavailable")

    monkeypatch.setattr("agent.sessions.execution_cache.logger.debug", broken_log)
    assert cache.publish(
        session, restore_rollout(session).commit, source_version=version, is_current=lambda: True
    )
    observed = cache.observation(session.session_id)
    assert observed is not None and observed.status == "stored"
    assert observed.maintenance_ms > 0
    assert observed.state_capture_ms > 0 and observed.serialization_ms > 0
    assert observed.guard_wait_ms >= 0 and observed.write_flush_ms > 0
    assert observed.atomic_publish_ms > 0 and observed.maintenance_cpu_ms >= 0
    loaded = cache.try_load(
        session.session_id,
        source_version=version,
        read_source=lambda: (version, sha256(source)),
        is_current=lambda: True,
    )
    assert loaded is not None
