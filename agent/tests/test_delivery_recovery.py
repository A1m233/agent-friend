from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.sessions.delivery_recovery import recover_delivery_history
from agent.sessions.events import Event, EventType
from agent.sessions.model_history import SessionModelHistory
from agent.sessions.rollout import RolloutRestoreResult, SessionRolloutRecorder, restore_rollout

from agent import JsonlSessionStore, SessionCorruptError

from .test_session_rollout import _create, _manager, _RecordingLLM, _run


def _broken_delivery(tmp_path: Path) -> tuple[JsonlSessionStore, str, RolloutRestoreResult]:
    store = JsonlSessionStore(tmp_path / "sessions")
    manager = _manager(tmp_path, _RecordingLLM(), store=store)
    sid = _create(manager)
    _run(manager, sid, "original question")
    manager.close()
    session = store.load_execution(sid)
    before = session.restored_rollout
    assert before is not None
    # Reproduce the old cold loader: reconstruct business history and start a
    # second root journal despite an already verified commit being available.
    share = Event(
        type="assistant_message",
        uuid="delivery-share",
        ts=datetime.now(UTC),
        payload={"content": "a new share"},
        meta={"message_source": {"kind": "agent_initiated"}},
    )
    store.append_event(sid, share)
    session.append(share)
    recorder = SessionRolloutRecorder(store, session)
    recorder.commit(
        history=SessionModelHistory.from_session(session),
        model_context=before.model_context,
        cache_epoch=before.cache_epoch,
        boundary="assistant_completed",
        turn_terminal=True,
        exact_ineligibility_reason="external_message",
    )
    assert restore_rollout(store.load(sid)).disposition == "corrupt_committed_history"
    return store, sid, before


def test_recovery_preserves_business_and_verified_model_prefix(tmp_path: Path) -> None:
    store, sid, before = _broken_delivery(tmp_path)
    path = store.base_dir / f"{sid}.jsonl"
    original = path.read_bytes()
    business = [e.to_jsonl() for e in store.load_business(sid).events]
    preview = recover_delivery_history(store, sid)
    assert not preview["applied"]
    assert path.read_bytes() == original
    assert not list(store.base_dir.glob("*.backup"))
    result = recover_delivery_history(store, sid, apply=True)
    assert result["applied"]
    assert Path(str(result["backup"])).read_bytes() == original
    repaired = store.load_execution(sid)
    assert [e.to_jsonl() for e in repaired.events] == business
    assert repaired.restored_rollout is not None
    items = repaired.restored_rollout.history.items
    assert [m.to_dict() for m in items[: len(before.history.items)]] == [
        m.to_dict() for m in before.history.items
    ]
    assert items[-1].content == "a new share"
    manager = _manager(tmp_path, _RecordingLLM())
    try:
        _run(manager, sid, "continue after recovery")
    finally:
        manager.close()
    assert restore_rollout(store.load(sid)).disposition == "exact"


@pytest.mark.parametrize("event_type", ["compaction", "tool_call_request", "turn_rewrite"])
def test_refuses_unsafe_suffix_without_touching_source(
    tmp_path: Path, event_type: EventType
) -> None:
    store, sid, _ = _broken_delivery(tmp_path)
    store.append_event(sid, Event(type=event_type, uuid="unsafe", ts=datetime.now(UTC), payload={}))
    path = store.base_dir / f"{sid}.jsonl"
    original = path.read_bytes()
    with pytest.raises(SessionCorruptError):
        recover_delivery_history(store, sid, apply=True)
    assert path.read_bytes() == original
    assert not list(store.base_dir.glob("*.backup"))


def test_corrupt_history_blocks_new_turn_and_delivery(tmp_path: Path) -> None:
    store, sid, _ = _broken_delivery(tmp_path)
    original = (store.base_dir / f"{sid}.jsonl").read_bytes()
    manager = _manager(tmp_path, _RecordingLLM())
    try:
        with pytest.raises(SessionCorruptError):
            manager.start_conversation(manager.open_for_execution(sid))
        with pytest.raises(SessionCorruptError):
            manager.append_delivered_message(
                sid,
                delivery_id="blocked",
                content="share",
                source_session_id="inner",
                source_run_id="wake",
            )
    finally:
        manager.close()
    assert (store.base_dir / f"{sid}.jsonl").read_bytes() == original
