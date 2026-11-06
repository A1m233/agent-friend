from pathlib import Path

import pytest
from agent.session_delivery import SessionDeliveryStore
from agent.sessions.manager import SessionManager
from agent.sessions.rollout import restore_rollout

from agent import JsonlSessionStore, Session

from .test_session_rollout import _create, _manager, _RecordingLLM, _run


@pytest.mark.parametrize("cold", [False, True])
def test_delivery_extends_existing_rollout_after_runtime_release(
    tmp_path: Path, cold: bool
) -> None:
    manager = _manager(tmp_path, _RecordingLLM())
    sid = _create(manager)
    _run(manager, sid, "previous question")
    store = manager._store
    previous = restore_rollout(store.load(sid))
    assert previous.commit is not None
    if cold:
        manager.close()
        manager = _manager(tmp_path, _RecordingLLM())
    try:
        manager.append_delivered_message(
            sid,
            delivery_id="next",
            content="prepared share",
            source_session_id="inner",
            source_run_id="wake",
        )
        session = store.load(sid)
        commit = [e for e in session.events if e.type == "rollout_commit"][-1]
        assert commit.payload["base_commit_id"] == previous.commit.commit_id
        restored = restore_rollout(session)
        assert restored.disposition != "corrupt_committed_history"
        assert [m.to_dict() for m in restored.history.items[: len(previous.history.items)]] == [
            m.to_dict() for m in previous.history.items
        ]
        assert any(m.content == "prepared share" for m in restored.history.items)
    finally:
        manager.close()


def test_prepared_message_persists_source_and_recovers_without_model_call(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    store.create(Session.new("Target", "default", "test/model", session_id="target"))
    manager = SessionManager(store)
    for _ in range(2):
        manager.append_delivered_message(
            "target",
            delivery_id="one",
            content="I found this",
            source_session_id="inner",
            source_run_id="wake",
        )
    session = store.load("target")
    assert len([e for e in session.events if e.type == "assistant_message"]) == 1
    assert not any(e.type == "user_message" for e in session.events)
    restored = restore_rollout(session)
    assert not restored.runtime_blocked
    assert [m.content for m in restored.history.items if m.role == "assistant"] == ["I found this"]
    assert any(m.meta.get("synthetic") == "agent_initiated_message" for m in restored.history.items)


def test_outbox_deduplicates_rephrased_tool_call_and_never_retries_unknown(tmp_path: Path) -> None:
    store = SessionDeliveryStore(tmp_path / "delivery.db")
    request = {
        "source_session_id": "inner",
        "source_run_id": "wake",
        "target_session_id": "target",
        "content": "message",
        "destination_id": "dest",
    }
    first = store.submit("call1", request)
    assert store.submit("call2", request)["delivery_id"] == first["delivery_id"]
    did = first["delivery_id"]
    store.update(did, conversation_state="committed")
    assert store.claim_im(did)
    assert not store.claim_im(did)
    recovered = SessionDeliveryStore(tmp_path / "delivery.db")
    assert recovered.get(did)["im_state"] == "unknown"
    assert recovered.pending() == []


def test_conversation_only_delivery_has_no_pending_im(tmp_path: Path) -> None:
    store = SessionDeliveryStore(tmp_path / "delivery.db")
    row = store.submit("call", {"content": "hello", "destination_id": None})
    store.update(row["delivery_id"], conversation_state="committed")
    assert store.get(row["delivery_id"])["im_state"] == "not_requested"
    assert store.pending() == []


def test_source_association_preserves_delivery_status_without_guessing_old_messages(
    tmp_path: Path,
) -> None:
    store = SessionDeliveryStore(tmp_path / "delivery.db")
    base = {
        "source_session_id": "inner",
        "source_run_id": "wake",
        "target_session_id": "target",
        "content": "消息",
        "destination_id": "qq",
    }
    old = store.submit("old", base)
    assert store.for_message("daily", "original") == []
    linked = store.submit(
        "linked", {**base, "source_messages": [{"session_id": "daily", "message_id": "original"}]}
    )
    store.update(
        linked["delivery_id"],
        conversation_state="committed",
        im_state="delivered",
        message_id="forwarded",
    )
    entries = store.for_message("daily", "original")
    assert [entry["delivery_id"] for entry in entries] == [linked["delivery_id"]]
    assert entries[0]["im_read_status"] == "unknown"
    assert entries[0]["im_state"] == "delivered"
    assert store.for_message("target", "forwarded") == entries
    assert old["delivery_id"] != linked["delivery_id"]
