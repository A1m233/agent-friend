from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from agent.runtime import SessionBusyError
from agent.sessions.manager import SessionManager
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.session_delivery.service import SessionDeliveryService

from agent import JsonlSessionStore, Session


@pytest.fixture
def service(tmp_path: Path) -> SessionDeliveryService:
    store = JsonlSessionStore(tmp_path / "sessions")
    for sid in ("source", "target"):
        store.create(Session.new(sid, "default", "test/model", session_id=sid))
    presence = SimpleNamespace(
        destination_id="qq-owner",
        session_id="target",
        im_type="qq",
        chat_scope="c2c",
        chat_id="owner",
        provider_bind_id="owner",
        online=True,
        last_interaction_at=None,
    )
    runtime = SimpleNamespace(
        settings=SimpleNamespace(sessions_dir=tmp_path / "sessions"),
        persistent_session_manager=SessionManager(store),
        im_runtime=SimpleNamespace(
            presence_snapshots=lambda: [presence],
            send_proactive=Mock(return_value=OutboundSendResult("delivered", "provider-id")),
        ),
        agent_runtime=SimpleNamespace(try_reserve_session_mutation=lambda _: nullcontext()),
        attachment_store=None,
        notification_service=None,
    )
    return SessionDeliveryService(runtime)  # type: ignore[arg-type]


def submit(service: SessionDeliveryService) -> dict[str, Any]:
    return service.submit(
        {
            "__agent_friend_current_session_id": "source",
            "__agent_friend_run_id": "wake",
            "__agent_friend_tool_call_id": "call",
            "target_session_id": "target",
            "content": "Prepared content",
            "notify_im": True,
            "destination_id": "qq-owner",
        }
    )


@pytest.mark.parametrize(
    ("source", "options", "expected"),
    [
        ("inner", {}, "delivered"),
        ("inner", {"notify_im": False}, "not_requested"),
        ("source", {}, "not_requested"),
    ],
)
def test_default_notification_depends_on_origin(
    service: SessionDeliveryService, source: str, options: dict[str, bool], expected: str
) -> None:
    service.runtime.persistent_session_manager.create(
        "default", "test/model", session_id="inner", origin_kind="inner_world"
    )
    args = {
        "__agent_friend_current_session_id": source,
        "__agent_friend_run_id": "wake",
        "__agent_friend_tool_call_id": "call",
        "target_session_id": "target",
        "content": "Prepared",
        **options,
    }
    row = service.submit(args)
    service.deliver_pending()
    receipt = service.store.get(row["delivery_id"])
    assert (receipt["conversation_state"], receipt["im_state"]) == ("committed", expected)
    im = service.runtime.im_runtime
    assert im is not None
    assert im.send_proactive.call_count == int(expected == "delivered")  # type: ignore[attr-defined]
    im.presence_snapshots = lambda: ()  # type: ignore[method-assign]
    assert service.submit(args)["delivery_id"] == row["delivery_id"]


def test_default_binding_lost_still_keeps_desktop_message(service: SessionDeliveryService) -> None:
    service.runtime.persistent_session_manager.create(
        "default", "test/model", session_id="inner", origin_kind="inner_world"
    )
    row = service.submit(
        {
            "__agent_friend_current_session_id": "inner",
            "__agent_friend_run_id": "w",
            "__agent_friend_tool_call_id": "a",
            "target_session_id": "target",
            "content": "new",
        }
    )
    service.runtime.im_runtime.presence_snapshots = lambda: ()  # type: ignore[union-attr,method-assign]
    service.deliver_pending()
    receipt = service.store.get(row["delivery_id"])
    assert (receipt["conversation_state"], receipt["im_state"]) == ("committed", "failed")
    service.runtime.im_runtime.send_proactive.assert_not_called()  # type: ignore[union-attr]


@pytest.mark.parametrize("outcome", ["delivered", "failed", "unknown"])
def test_im_result_is_separate_from_committed_message_and_never_resent(
    service: SessionDeliveryService, outcome: str
) -> None:
    service.runtime.im_runtime.send_proactive.return_value = OutboundSendResult(outcome)  # type: ignore[union-attr,arg-type]
    row = submit(service)
    service.deliver_pending()
    receipt = service.store.get(row["delivery_id"])
    assert receipt["conversation_state"] == "committed"
    assert receipt["im_state"] == outcome
    service.deliver_pending()
    service.runtime.im_runtime.send_proactive.assert_called_once()  # type: ignore[union-attr]
    events = service.runtime.persistent_session_manager.open("target").events
    assert len([e for e in events if e.type == "assistant_message"]) == 1


def test_paused_contact_cancels_stale_queue_even_after_resume(
    service: SessionDeliveryService,
) -> None:
    row = submit(service)
    stale = service.store.get(row["delivery_id"])
    service.store.cancel_source("source")
    service._deliver(stale)
    assert service.store.get(row["delivery_id"])["conversation_state"] == "cancelled"
    service.runtime.im_runtime.send_proactive.assert_not_called()  # type: ignore[union-attr]
    assert not any(
        e.type == "assistant_message"
        for e in service.runtime.persistent_session_manager.open("target").events
    )


def test_binding_change_does_not_redirect_or_append(service: SessionDeliveryService) -> None:
    row = submit(service)
    service.runtime.im_runtime.presence_snapshots = lambda: ()  # type: ignore[union-attr,method-assign]
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["conversation_state"] == "failed"
    service.runtime.im_runtime.send_proactive.assert_not_called()  # type: ignore[union-attr]


def test_target_busy_defers_without_starting_another_model(service: SessionDeliveryService) -> None:
    row = submit(service)
    service.runtime.agent_runtime.try_reserve_session_mutation = Mock(  # type: ignore[union-attr,method-assign]
        side_effect=SessionBusyError("target")
    )
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["conversation_state"] == "queued"
    service.runtime.im_runtime.send_proactive.assert_not_called()  # type: ignore[union-attr]


def test_deleted_source_purges_retained_prepared_body(service: SessionDeliveryService) -> None:
    row = submit(service)
    service.store.forget_session("source")
    assert service.store.pending() == []
    with pytest.raises(ValueError, match="delivery_not_found"):
        service.store.get(row["delivery_id"])
