import base64
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from agent.attachments import AttachmentStore
from agent.runtime import SessionBusyError
from agent.session_delivery import SessionDeliveryStore
from agent.sessions.access.service import ConversationAccessError, ConversationAccessService
from agent.sessions.manager import SessionManager
from agent.tools.builtin.conversation_access.delivery import SendConversationMessageTool
from agent.tools.registry import ToolRegistry
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.session_delivery.service import SessionDeliveryService

from agent import JsonlSessionStore, Session, SessionMetadataStore


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
        persistent_session_manager=SessionManager(
            store, metadata_store=SessionMetadataStore(tmp_path / "metadata.json")
        ),
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


def test_source_reference_requires_actual_read_by_current_caller(
    service: SessionDeliveryService,
) -> None:
    manager = service.runtime.persistent_session_manager
    manager.append_delivered_message(
        "target",
        delivery_id="original",
        content="原始结果",
        source_session_id="source",
        source_run_id="previous",
    )
    access = ConversationAccessService(manager._store)
    service.source_was_read = access.has_read_message
    message = next(
        e for e in manager.query("target").active_events if e.type == "assistant_message"
    )
    args = {
        "__agent_friend_current_session_id": "source",
        "__agent_friend_run_id": "wake",
        "__agent_friend_tool_call_id": "share",
        "target_session_id": "target",
        "content": "更新",
        "source_messages": [{"session_id": "target", "message_id": message.uuid}],
    }
    with pytest.raises(ConversationAccessError, match="来源未读取"):
        service.submit(args)
    access.read_conversation(session_id="target", current_session_id="other")
    with pytest.raises(ConversationAccessError):
        service.submit(args)
    access.read_conversation(session_id="target", current_session_id="source")
    result = service.submit(args)
    assert (
        service.store.get(result["delivery_id"])["request"]["source_messages"]
        == args["source_messages"]
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


def test_target_busy_sends_im_then_commits_once_after_expiry(
    service: SessionDeliveryService,
) -> None:
    row = submit(service)
    service.runtime.agent_runtime.try_reserve_session_mutation = Mock(  # type: ignore[union-attr,method-assign]
        side_effect=SessionBusyError("target")
    )
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["conversation_state"] == "queued"
    assert service.store.get(row["delivery_id"])["im_state"] == "delivered"
    service.runtime.im_runtime.send_proactive.assert_called_once()  # type: ignore[union-attr]
    with service.store.connection() as db:
        db.execute(
            "UPDATE deliveries SET expires_at=?",
            ((datetime.now(UTC) - timedelta(hours=2)).isoformat(),),
        )
    service.store = SessionDeliveryStore(service.store.path)
    service.runtime.agent_runtime.try_reserve_session_mutation = lambda _: nullcontext()  # type: ignore[union-attr,method-assign]
    service.deliver_pending()
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["conversation_state"] == "committed"
    service.runtime.im_runtime.send_proactive.assert_called_once()  # type: ignore[union-attr]
    messages = [
        e
        for e in service.runtime.persistent_session_manager.open("target").events
        if e.type == "assistant_message"
    ]
    assert len(messages) == 1


def test_deleted_source_purges_retained_prepared_body(service: SessionDeliveryService) -> None:
    row = submit(service)
    service.store.forget_session("source")
    assert service.store.pending() == []
    with pytest.raises(ValueError, match="delivery_not_found"):
        service.store.get(row["delivery_id"])


@pytest.mark.parametrize("busy", [False, True])
def test_offline_submit_recovers_without_duplicate_send(
    service: SessionDeliveryService, busy: bool
) -> None:
    im = service.runtime.im_runtime
    assert im is not None
    presence = im.presence_snapshots()[0]
    presence.online = False  # type: ignore[misc]
    if busy:
        service.runtime.agent_runtime.try_reserve_session_mutation = Mock(  # type: ignore[union-attr,method-assign]
            side_effect=SessionBusyError("target")
        )
    row = submit(service)
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["im_state"] == "pending"
    im.send_proactive.assert_not_called()  # type: ignore[attr-defined]
    presence.online = True  # type: ignore[misc]
    service.deliver_pending()
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["im_state"] == "delivered"
    assert service.store.get(row["delivery_id"])["error"] is None
    im.send_proactive.assert_called_once()  # type: ignore[attr-defined]


@pytest.mark.parametrize("error", ["provider_unavailable", "runtime_stopped"])
def test_disconnect_between_presence_check_and_send_is_retried(
    service: SessionDeliveryService, error: str
) -> None:
    im = service.runtime.im_runtime
    assert im is not None
    im.send_proactive.side_effect = [  # type: ignore[attr-defined]
        OutboundSendResult("failed", error_kind=error),
        OutboundSendResult("delivered", "receipt"),
    ]
    row = submit(service)
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["im_state"] == "pending"
    service.deliver_pending()
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["im_state"] == "delivered"
    assert im.send_proactive.call_count == 2  # type: ignore[attr-defined]


@pytest.mark.parametrize("busy", [False, True])
def test_expiry_reports_failure_to_source_and_does_not_send_after_reconnect(
    service: SessionDeliveryService, busy: bool
) -> None:
    im = service.runtime.im_runtime
    assert im is not None
    im.presence_snapshots()[0].online = False  # type: ignore[misc]
    row = submit(service)

    def reserve(sid: str) -> Any:
        if busy and sid == "target":
            raise SessionBusyError("target")
        return nullcontext()

    service.runtime.agent_runtime.try_reserve_session_mutation = reserve  # type: ignore[union-attr,method-assign]
    service.deliver_pending()
    with service.store.connection() as db:
        db.execute(
            "UPDATE deliveries SET expires_at=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
    service.deliver_pending()
    im.presence_snapshots()[0].online = True  # type: ignore[misc]
    service.deliver_pending()
    saved = service.store.get(row["delivery_id"])
    assert saved["conversation_state"] == ("expired" if busy else "committed")
    assert saved["feedback_state"] == "delivered"
    assert saved["updated_at"]
    im.send_proactive.assert_not_called()  # type: ignore[attr-defined]
    messages = [
        e
        for e in service.runtime.persistent_session_manager.open("source").events
        if e.type == "assistant_message"
    ]
    assert len(messages) == 1
    assert "IM 未发送" in messages[0].payload["content"]
    assert "超过有效期" in messages[0].payload["content"]


def test_uncertain_send_survives_restart_and_reports_once_when_source_idle(
    service: SessionDeliveryService,
) -> None:
    row = submit(service)
    # Crash after claiming an external send while the target is still busy.
    assert service.store.claim_im(row["delivery_id"])
    service.store = SessionDeliveryStore(service.store.path)

    def reserve(sid: str) -> Any:
        if sid == "source":
            raise SessionBusyError("source")
        return nullcontext()

    service.runtime.agent_runtime.try_reserve_session_mutation = reserve  # type: ignore[union-attr,method-assign]
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["feedback_state"] == "pending"
    service.store = SessionDeliveryStore(service.store.path)
    service.runtime.agent_runtime.try_reserve_session_mutation = lambda _: nullcontext()  # type: ignore[union-attr,method-assign]
    service.deliver_pending()
    service.deliver_pending()
    service.runtime.im_runtime.send_proactive.assert_not_called()  # type: ignore[union-attr]
    saved = service.store.get(row["delivery_id"])
    assert saved["im_state"] == "unknown"
    assert saved["feedback_state"] == "delivered"
    messages = [
        e
        for e in service.runtime.persistent_session_manager.open("source").events
        if e.type == "assistant_message"
    ]
    assert len(messages) == 1
    assert "发送结果未知" in messages[0].payload["content"]


def test_conversation_only_waits_for_idle_without_sending_im(
    service: SessionDeliveryService,
) -> None:
    row = service.submit(
        {
            "__agent_friend_current_session_id": "source",
            "__agent_friend_run_id": "wake",
            "__agent_friend_tool_call_id": "only-chat",
            "target_session_id": "target",
            "content": "hello",
            "notify_im": False,
        }
    )
    service.runtime.agent_runtime.try_reserve_session_mutation = Mock(  # type: ignore[union-attr,method-assign]
        side_effect=SessionBusyError("target")
    )
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["conversation_state"] == "queued"
    service.runtime.im_runtime.send_proactive.assert_not_called()  # type: ignore[union-attr]


def test_feedback_crash_after_append_is_idempotent(
    service: SessionDeliveryService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    im = service.runtime.im_runtime
    assert im is not None
    im.send_proactive.return_value = OutboundSendResult("unknown", error_kind="send_timeout")  # type: ignore[attr-defined]
    row = submit(service)
    update = service.store.update

    def crash_before_receipt(delivery_id: str, **values: Any) -> None:
        if values.get("feedback_state") == "delivered":
            raise RuntimeError("crash after feedback append")
        update(delivery_id, **values)

    monkeypatch.setattr(service.store, "update", crash_before_receipt)
    with pytest.raises(RuntimeError, match="crash after feedback"):
        service.deliver_pending()
    service.store = SessionDeliveryStore(service.store.path)
    service.deliver_pending()
    assert service.store.get(row["delivery_id"])["feedback_state"] == "delivered"
    messages = [
        e
        for e in service.runtime.persistent_session_manager.open("source").events
        if e.type == "assistant_message"
    ]
    assert len(messages) == 1
    im.send_proactive.assert_called_once()  # type: ignore[attr-defined]


def test_archived_target_is_not_notified(service: SessionDeliveryService) -> None:
    row = submit(service)
    service.runtime.persistent_session_manager.archive("target")
    assert service.runtime.persistent_session_manager.is_archived("target")
    service.deliver_pending()
    saved = service.store.get(row["delivery_id"])
    assert saved["conversation_state"] == "failed"
    assert saved["feedback_state"] == "delivered"
    service.runtime.im_runtime.send_proactive.assert_not_called()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "content",
    [
        "前文\n![图片](https://example.invalid/picture.png)\n后文",
        "语法示例：\n```markdown\n![图片](C:/private/image.png)\n```\n\\![转义](file_ref)",
    ],
)
def test_public_markdown_delivery_keeps_body_and_assistant_role_after_reload(
    service: SessionDeliveryService,
    content: str,
) -> None:
    tool = SendConversationMessageTool()
    tool.submit = service.submit
    result = ToolRegistry([tool]).invoke(
        tool.name,
        {
            "__agent_friend_current_session_id": "source",
            "__agent_friend_run_id": "wake",
            "__agent_friend_tool_call_id": "markdown",
            "target_session_id": "target",
            "content": content,
            "notify_im": True,
            "destination_id": "qq-owner",
        },
    )
    assert not result.is_error
    delivery_id = json.loads(result.text)["delivery_id"]
    service.deliver_pending()
    service.deliver_pending()
    receipt = service.store.get(delivery_id)
    assert (receipt["conversation_state"], receipt["im_state"]) == ("committed", "delivered")
    im = service.runtime.im_runtime
    assert im is not None
    send = cast(Mock, im.send_proactive)
    send.assert_called_once()
    assert send.call_args.args == ("qq-owner", content)
    assert send.call_args.kwargs["text_format"] == "markdown"
    assert send.call_args.kwargs["image"] is None

    restored = JsonlSessionStore(service.runtime.settings.sessions_dir).load("target")
    assert not any(e.type == "user_message" for e in restored.events)
    messages = [e for e in restored.events if e.type == "assistant_message"]
    assert len(messages) == 1
    assert messages[0].payload["content"] == content
    assert messages[0].payload["attachments"] == []
    assert messages[0].meta["message_source"]["kind"] == "agent_initiated"
    assert [m.content for m in restored.messages if m.role == "assistant"] == [content]


def test_legacy_queued_image_survives_service_restart_and_delivers_once(
    service: SessionDeliveryService,
    tmp_path: Path,
) -> None:
    assets = AttachmentStore(tmp_path / "assets")
    _, ref = assets.import_image_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        ),
        display_name="legacy.png",
    )
    # Seed the durable request already accepted by an older public tool contract.
    row = service.store.submit(
        "legacy-accepted",
        {
            "source_session_id": "source",
            "source_run_id": "old-run",
            "target_session_id": "target",
            "content": "旧版已接受的图片",
            "destination_id": "qq-owner",
            "attachments": [ref.to_dict()],
        },
    )
    cast(SimpleNamespace, service.runtime).attachment_store = AttachmentStore(tmp_path / "assets")
    restarted = SessionDeliveryService(service.runtime)
    restarted.deliver_pending()
    restarted.deliver_pending()
    receipt = restarted.store.get(row["delivery_id"])
    assert (receipt["conversation_state"], receipt["im_state"]) == ("committed", "delivered")
    im = service.runtime.im_runtime
    assert im is not None
    send = cast(Mock, im.send_proactive)
    send.assert_called_once()
    image = send.call_args.kwargs["image"]
    assert image.path == assets.verify(ref.asset_id)
    assert image.display_name == "legacy.png"
    restored = JsonlSessionStore(service.runtime.settings.sessions_dir).load("target")
    messages = [e for e in restored.events if e.type == "assistant_message"]
    assert len(messages) == 1
    assert messages[0].payload["attachments"] == [ref.to_dict()]
    assistant = [m for m in restored.messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].attachments == [ref]
