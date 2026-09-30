from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.sessions import (
    Event,
    Session,
    SessionCacheEpoch,
    SessionModelContext,
    SessionModelHistory,
    SessionRolloutRecorder,
)
from agent.sessions.errors import SessionCorruptError, SessionWriterConflictError
from agent.sessions.maintenance import compact_session
from agent.sessions.rollout_encoding import RolloutDecoder, RolloutEncoder
from agent.sessions.store import JsonlSessionStore


def legacy(store: JsonlSessionStore) -> str:
    session = Session.new(title="test", persona="default", model="m")
    store.create(session)
    recorder = SessionRolloutRecorder(store, session)
    history = SessionModelHistory.from_session(session)
    for _ in range(3):
        recorder.commit(
            history=history,
            model_context=SessionModelContext(),
            cache_epoch=SessionCacheEpoch(),
            boundary="manual_checkpoint",
            turn_terminal=True,
        )
    # Materialize original logical v1 events as the old-data fixture.
    path = store.base_dir / f"{session.session_id}.jsonl"
    path.write_text("".join(event.to_jsonl() + "\n" for event in session.events), encoding="utf-8")
    return session.session_id


def test_compaction_preview_and_apply_preserve_history_and_keep_backup(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    sid = legacy(store)
    path = tmp_path / f"{sid}.jsonl"
    original = path.read_bytes()
    preview = compact_session(store, sid)
    assert preview["equivalent"] and not preview["applied"]
    assert path.read_bytes() == original
    result = compact_session(store, sid, apply=True)
    assert result["applied"]
    assert Path(str(result["backup"])).read_bytes() == original
    assert store.load_execution(sid).restored_rollout.disposition == "exact"  # type: ignore[union-attr]
    assert compact_session(store, sid)["equivalent"]


def test_failed_publication_keeps_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JsonlSessionStore(tmp_path)
    sid = legacy(store)
    path = tmp_path / f"{sid}.jsonl"
    original = path.read_bytes()

    def fail(*args: object) -> None:
        raise OSError("publication failed")

    monkeypatch.setattr("agent.sessions.maintenance.os.replace", fail)
    with pytest.raises(OSError, match="publication failed"):
        compact_session(store, sid, apply=True)
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.backup"))
    assert compact_session(store, sid)["equivalent"]


def test_maintenance_refuses_active_writer_and_corrupt_input(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    sid = legacy(store)
    lease = store.acquire_writer_lease(sid)
    try:
        with pytest.raises(SessionWriterConflictError):
            compact_session(JsonlSessionStore(tmp_path), sid, apply=True)
    finally:
        lease.release()
    with (tmp_path / f"{sid}.jsonl").open("ab") as file:
        file.write(b"broken")
    with pytest.raises(SessionCorruptError):
        compact_session(store, sid, apply=True)


def test_version_references_and_non_prefix_deltas_are_verified(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    sid = legacy(store)
    events = [e for e in store.load(sid).events if e.type == "rollout_commit"]
    encoder, decoder = RolloutEncoder(), RolloutDecoder()
    for event, identities in zip(events, [["a", "b"], ["a", "c"], ["x"]], strict=True):
        event.payload["active_event_uuids"] = identities
        encoded = encoder.encode(event)
        decoded = [decoder.decode(record) for record in encoded][-1]
        assert decoded == event
    with pytest.raises(SessionCorruptError, match="unresolved"):
        RolloutDecoder().decode(encoded[-1])
    tampered = json.loads(encoded[-1].to_jsonl())
    tampered["payload"]["active_event_delta"]["suffix"] = ["forged"]
    with pytest.raises(SessionCorruptError, match="digest"):
        decoder.decode(Event.from_jsonl(json.dumps(tampered)))
