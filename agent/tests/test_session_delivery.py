from pathlib import Path

from agent.session_delivery import SessionDeliveryStore
from agent.sessions.manager import SessionManager
from agent.sessions.rollout import restore_rollout

from agent import JsonlSessionStore, Session


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
