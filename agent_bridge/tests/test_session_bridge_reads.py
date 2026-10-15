"""Lightweight IM polling and outbound reads never restore full session history."""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from agent_bridge.attachment_reconciliation import reconcile_attachments
from agent_bridge.session_bridge import SessionBridge

from agent import AttachmentStore, Event, JsonlSessionStore, Session, SessionManager


def test_polling_and_outbound_reads_use_presence_and_active_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonlSessionStore(tmp_path)
    session = Session.new("probe", "default", "test/model")
    store.create(session)
    user = Event(type="user_message", uuid="user", ts=session.created_at, payload={"content": "hi"})
    answer = Event(
        type="assistant_message", uuid="answer", ts=session.created_at, payload={"content": "hello"}
    )
    store.append_events(session.session_id, [user, answer])
    manager = SessionManager(store=store)
    runtime = MagicMock()
    runtime.persistent_session_manager = manager
    bridge = SessionBridge(runtime)

    def no_full_load(*args: object, **kwargs: object) -> Session:
        raise AssertionError("polling must not load full history")

    monkeypatch.setattr(manager, "open", no_full_load)
    monkeypatch.setattr(store, "load", no_full_load)
    assert bridge.session_exists(session.session_id)
    assert not bridge.session_exists("missing")
    assert bridge.persistent_model(session.session_id, default="fallback") == "test/model"
    assert bridge.persistent_model("missing", default="fallback") == "fallback"
    assert bridge.persistent_event(session.session_id, "answer") == answer
    assert bridge.assistant_events_after(session.session_id, "user") == (answer,)

    store.append_event(
        session.session_id,
        Event(
            type="turn_rewrite",
            uuid="rewrite",
            ts=session.created_at,
            payload={"inactive_event_uuids": ["answer"]},
        ),
    )
    assert bridge.persistent_event(session.session_id, "answer") is None
    assert bridge.assistant_events_after(session.session_id, "user") == ()
    store.delete(session.session_id)
    assert not bridge.session_exists(session.session_id)


def test_generated_image_ownership_integrity_gc_and_receipt(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path / "sessions")
    assets = AttachmentStore(tmp_path / "assets", draft_ttl_seconds=0)
    _, ref = assets.import_image_bytes(b"\x89PNG\r\n\x1a\noriginal", display_name="generated.png")
    os.utime(assets.metadata_dir / f"{ref.asset_id}.json", (0, 0))
    session = Session.new("image", "default", "test/model")
    store.create(session)
    store.append_event(
        session.session_id,
        Event(
            type="assistant_message",
            uuid="image",
            ts=session.created_at,
            payload={
                "generated_image": {"error_code": None},
                "attachments": [ref.to_dict()],
                "content": "host fact",
                "item_index": 0,
            },
            meta={"run_id": "run"},
        ),
    )
    runtime = MagicMock()
    runtime.persistent_session_manager = SessionManager(store=store)
    runtime.persistent_store = store
    runtime.attachment_store = assets
    runtime.scheduled_task_store = None
    runtime.session_request_service = None
    bridge = SessionBridge(runtime)
    reconciled = reconcile_attachments(runtime)
    assert reconciled is not None and reconciled["removed_assets"] == 0
    path, name = bridge.generated_image_path(session.session_id, "image")
    assert path.read_bytes().endswith(b"original") and name == "generated.png"
    with pytest.raises(ValueError, match="image_reference_invalid"):
        bridge.generated_image_path("other-session", "image")
    bridge.record_image_delivery(
        session.session_id,
        run_id="run",
        source="image",
        part_index=0,
        outcome="unknown",
        error_kind="timeout",
    )
    saved = store.load(session.session_id)
    receipt = saved.events[-1]
    assert receipt.type == "image_delivery" and receipt.payload["outcome"] == "unknown"
    assert receipt.payload["source_message_ref"] == "image"
    assert len(saved.messages) == 1
    path.write_bytes(b"\x89PNG\r\n\x1a\nmodified")
    with pytest.raises(ValueError, match="image_integrity_failed"):
        bridge.generated_image_path(session.session_id, "image")
