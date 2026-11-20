"""Prepared-message delivery using existing session writers and IM providers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent.attachments import AttachmentError
from agent.errors import AgentError
from agent.message_exposure import content_version
from agent.paths import session_delivery_db_path
from agent.runtime import SessionBusyError
from agent.session_delivery import SessionDeliveryStore
from agent.sessions.access.service import ConversationAccessError
from agent.sessions.errors import SessionNotFoundError, SessionWriterConflictError
from agent.sessions.manager import delivered_message_events

from ..protocols.im.delivery.once import DeliveryAttempt, deliver_once

if TYPE_CHECKING:
    from ..assembly import BridgeRuntime
    from ..protocols.im.provider import OutboundSendResult

logger = logging.getLogger(__name__)


class SessionDeliveryService:
    def __init__(self, runtime: BridgeRuntime) -> None:
        self.runtime = runtime
        self.store = SessionDeliveryStore(
            session_delivery_db_path(runtime.settings.sessions_dir.parent)
        )
        self.contact_allowed: Callable[[str], bool] = lambda _sid: True
        self.may_introduce: Callable[[str, str], bool] = lambda *_: False
        self.source_was_read: Callable[[str, str, str, str], bool] = lambda *_args: False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._delivery_lock = threading.Lock()
        self.contact_lock = threading.RLock()

    def destinations(self) -> list[dict[str, Any]]:
        im = self.runtime.im_runtime
        if im is None:
            return []
        return [
            {
                "destination_id": p.destination_id,
                "session_id": p.session_id,
                "channel": p.im_type,
                "recipient": "owner"
                if p.im_type == "qq" and p.chat_scope == "c2c" and p.chat_id == p.provider_bind_id
                else "unverified",
                "online": p.online,
                "proactive_supported": p.im_type == "qq" and p.chat_scope == "c2c",
                "last_user_interaction_at": p.last_interaction_at.isoformat()
                if p.last_interaction_at
                else None,
            }
            for p in im.presence_snapshots()
        ]

    def submit(self, args: dict[str, Any]) -> dict[str, Any]:
        source = str(args.get("__agent_friend_current_session_id", ""))
        run_id = str(args.get("__agent_friend_run_id", ""))
        call_id = str(args.get("__agent_friend_tool_call_id", ""))
        if not source or not run_id or not call_id:
            raise ConversationAccessError("run_context_missing", "投递只能由当前有效运行发起。")
        if not self.contact_allowed(source):
            raise ConversationAccessError(
                "contact_paused", "自主联系已暂停，请安静结束，不要换通道重试。"
            )
        target = str(args.get("target_session_id", ""))
        if not target:
            if not self.may_introduce(source, run_id):
                raise ConversationAccessError(
                    "target_missing", "请选择 list_conversations 返回的目标会话。"
                )
            if (
                args.get("destination_id")
                or args.get("notify_im")
                or args.get("source_messages")
                or args.get("attachment_asset_ids")
            ):
                raise ConversationAccessError("invalid_intro", "首次招呼只发送问候正文。")
            request = {
                "first_contact": True,
                "source_session_id": source,
                "source_run_id": run_id,
                "target_session_id": "",
                "destination_id": None,
                "content": str(args["content"]),
                "auto_qq": "notify_im" not in args,
            }
            with self.contact_lock:
                if not self.contact_allowed(source):
                    raise ConversationAccessError("contact_paused", "自主联系已暂停。")
                return self.receipt(self.store.submit(f"{source}:{run_id}:first-contact", request))
        if target == source:
            raise ConversationAccessError(
                "same_conversation", "当前会话直接回复即可，无需跨会话发送。"
            )
        sessions = self.runtime.persistent_session_manager
        if not sessions.exists(target) or sessions.is_archived(target) or sessions.is_inner(target):
            raise ConversationAccessError("target_missing", "目标已不可用，请重新列出会话。")
        if not sessions.exists(source):
            raise ConversationAccessError("source_missing", "来源会话已不存在，不能继续投递。")
        destination = args.get("destination_id") if args.get("notify_im") else None
        if destination:
            self._validate_destination(target, str(destination), require_online=False)
        attachments = []
        asset_ids = args.get("attachment_asset_ids", [])
        if asset_ids:
            source_session = sessions.query(source)
            known = {
                str(item.get("asset_id"))
                for event in source_session.active_events
                for key in ("attachments", "images")
                for item in event.payload.get(key, [])
                if isinstance(item, dict)
            }
            assets = self.runtime.attachment_store
            if assets is None or not set(asset_ids) <= known:
                raise ConversationAccessError(
                    "attachment_unavailable",
                    "只允许使用当前会话已经取得的附件引用，请先读取或生成实际素材。",
                )
            for asset_id in asset_ids:
                assets.verify(asset_id)
                attachments.append(assets.metadata(asset_id).to_dict())
            if destination and (len(attachments) != 1 or attachments[0]["kind"] != "image"):
                raise ConversationAccessError(
                    "im_attachment_unsupported", "IM 当前支持单张图片；请仅向会话投递其它附件。"
                )
        request = {
            "source_session_id": source,
            "source_run_id": run_id,
            "target_session_id": target,
            "content": str(args["content"]),
            "destination_id": destination,
            "attachments": attachments,
        }
        if refs := args.get("source_messages"):
            if not isinstance(refs, list) or len(refs) > 8:
                raise ConversationAccessError("invalid_sources", "最多关联八条已读取消息。")
            for ref in refs:
                if not isinstance(ref, dict) or set(ref) != {"session_id", "message_id"}:
                    raise ConversationAccessError(
                        "invalid_sources", "来源需要精确session_id和message_id。"
                    )
                sid, mid = ref["session_id"], ref["message_id"]
                if not isinstance(sid, str) or not isinstance(mid, str) or not sessions.exists(sid):
                    raise ConversationAccessError("source_missing", "来源消息不可用，请重新读取。")
                event = next((e for e in sessions.query(sid).active_events if e.uuid == mid), None)
                if event is None or not self.source_was_read(
                    source, sid, mid, content_version(str(event.payload.get("content", "")))
                ):
                    raise ConversationAccessError(
                        "source_not_read", "来源未读取或已变化，请先用read_conversation读取原消息。"
                    )
            request["source_messages"] = refs
        if (
            "notify_im" not in args
            and sessions.is_inner(source)
            and (not attachments or (len(attachments) == 1 and attachments[0]["kind"] == "image"))
        ):
            request["auto_qq"] = True
            request["destination_id"] = self._preferred_qq(target)
        row = self.store.submit(f"{source}:{run_id}:{call_id}", request)
        return self.receipt(row)

    def _preferred_qq(self, target: str) -> str | None:
        candidates = [
            p
            for p in self.destinations()
            if p["session_id"] == target
            and p["channel"] == "qq"
            and p["recipient"] == "owner"
            and p["proactive_supported"]
        ]
        preferred = max(
            candidates,
            key=lambda p: (
                p["online"],
                p["last_user_interaction_at"] or "",
                p["destination_id"],
            ),
            default=None,
        )
        return str(preferred["destination_id"]) if preferred else None

    @staticmethod
    def receipt(row: dict[str, Any]) -> dict[str, Any]:
        return {k: row[k] for k in ("delivery_id", "conversation_state", "im_state", "error")}

    def _validate_destination(
        self, target: str, destination: str, *, require_online: bool = True
    ) -> bool:
        found = next(
            (
                p
                for p in self.destinations()
                if p["destination_id"] == destination and p["session_id"] == target
            ),
            None,
        )
        if not found or found["recipient"] != "owner":
            raise ConversationAccessError(
                "binding_changed", "通知去向不再属于此用户会话，请重新发现绑定；不要猜测接收者。"
            )
        if not found["proactive_supported"] or (require_online and not found["online"]):
            raise ConversationAccessError(
                "destination_unavailable", "所选IM暂不能主动发送，可只投递会话或稍后再决定。"
            )
        return bool(found["online"])

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="SessionDelivery")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.deliver_pending()
            except Exception:
                logger.exception("prepared message delivery failed")
            self._stop.wait(1)

    def deliver_pending(self) -> None:
        if not self._delivery_lock.acquire(blocking=False):
            return
        try:
            for row in self.store.pending():
                if self._stop.is_set():
                    return
                self._deliver(row)
            for row in self.store.pending_feedback():
                if self._stop.is_set():
                    return
                self._report_failure(row)
        finally:
            self._delivery_lock.release()

    def _deliver(self, row: dict[str, Any]) -> None:
        if row["request"].get("first_contact"):
            with self.contact_lock:
                self._deliver_ready(row)
        else:
            self._deliver_ready(row)

    def _resolve_first_target(self, row: dict[str, Any]) -> bool:
        """Resolve at delivery time. A newly published conversation already contains its greeting."""
        manager = self.runtime.persistent_session_manager
        did = row["delivery_id"]
        request = row["request"]
        with manager.creation_lock:
            active = manager.list(navigation=True)
            # A crash may have published the greeting before the outbox pinned its target.
            if manager.exists(did):
                target = did
            elif active:
                target = active[0].session_id
            elif manager.list(archived=True, navigation=True):
                return False
            else:
                persona = self.runtime.catalog.find_by_name(self.runtime.default_persona)
                events = delivered_message_events(
                    did, request["content"], request["source_session_id"], request["source_run_id"]
                )
                target = manager.create(
                    persona.name,
                    self.runtime.default_model,
                    persona_id=persona.id,
                    session_id=did,
                    initial_events=tuple(events),
                ).session_id
            destination = self._preferred_qq(target) if request["auto_qq"] else None
            self.store.bind_first_target(did, target, destination)
            return True

    def _deliver_ready(self, row: dict[str, Any]) -> None:
        row = self.store.get(row["delivery_id"])
        if row["conversation_state"] != "queued" and not (
            row["conversation_state"] == "committed" and row["im_state"] == "pending"
        ):
            return
        request = row["request"]
        did = row["delivery_id"]

        def close(reason: str, state: str) -> None:
            current = self.store.get(did)
            self.store.update(
                did,
                conversation_state=state
                if current["conversation_state"] == "queued"
                else current["conversation_state"],
                im_state="cancelled" if current["im_state"] == "pending" else current["im_state"],
                error=reason,
            )

        if not self.contact_allowed(request["source_session_id"]):
            close("contact_paused", "cancelled")
            return
        if not self.runtime.persistent_session_manager.exists(request["source_session_id"]):
            close("source_missing", "cancelled")
            return
        # A possibly delivered IM still needs its eventual conversation copy.
        # Expiry bounds new external sends, not persistence of an existing send.
        if datetime.now(UTC) >= datetime.fromisoformat(row["expires_at"]) and row[
            "im_state"
        ] not in {"delivered", "unknown", "sending"}:
            close("delivery_expired", "expired")
            return
        runtime = self.runtime
        if request.get("first_contact") and not request["target_session_id"]:
            if runtime.agent_runtime is None:
                return
            if not self._resolve_first_target(row):
                close("only_archived_conversations", "cancelled")
                return
            row = self.store.get(did)
            request = row["request"]
        target = request["target_session_id"]
        try:
            manager = runtime.persistent_session_manager
            if (
                not manager.exists(target)
                or manager.is_archived(target)
                or manager.is_inner(target)
            ):
                raise ConversationAccessError("target_missing", "目标会话已不可用。")
            if (
                request["destination_id"]
                and not request.get("auto_qq")
                and runtime.im_runtime
                and row["im_state"] == "pending"
            ):
                self._validate_destination(target, request["destination_id"], require_online=False)
            if row["conversation_state"] == "queued":
                self._commit_conversation(row)
            self._deliver_im(self.store.get(did))
        except (ConversationAccessError, SessionNotFoundError, AgentError, AttachmentError) as exc:
            close(str(exc), "failed")

    def _commit_conversation(self, row: dict[str, Any]) -> None:
        runtime = self.runtime
        if runtime.agent_runtime is None:
            return
        request = row["request"]
        did, target = row["delivery_id"], request["target_session_id"]
        try:
            with runtime.agent_runtime.try_reserve_session_mutation(target), self.contact_lock:
                if not self.contact_allowed(request["source_session_id"]):
                    return
                if self.store.get(did)["conversation_state"] != "queued":
                    return
                for ref in request.get("attachments", []):
                    if runtime.attachment_store is None:
                        raise AttachmentError("attachment_unavailable", "附件原件不可用。")
                    runtime.attachment_store.verify(ref["asset_id"])
                message_id = runtime.persistent_session_manager.append_delivered_message(
                    target,
                    delivery_id=did,
                    content=request["content"],
                    source_session_id=request["source_session_id"],
                    source_run_id=request["source_run_id"],
                    attachments=request.get("attachments", []),
                )
                if runtime.notification_service is not None:
                    runtime.notification_service.on_prepared_message(
                        target,
                        did,
                        request["content"],
                        datetime.fromisoformat(row["created_at"]),
                    )
                self.store.update(did, conversation_state="committed", message_id=message_id)
        except (SessionBusyError, SessionWriterConflictError):
            # IM delivery has its own durable claim and can proceed while this waits.
            return

    def _deliver_im(self, row: dict[str, Any]) -> None:
        from ..protocols.im.content import OutboundImage

        im = self.runtime.im_runtime
        if im is None or row["im_state"] != "pending":
            return
        request, did = row["request"], row["delivery_id"]
        try:
            online = self._validate_destination(
                request["target_session_id"],
                request["destination_id"],
                require_online=False,
            )
        except ConversationAccessError as exc:
            self.store.update(did, im_state="failed", error=exc.code)
            return
        if not online:
            self.store.update(did, error="provider_unavailable")
            return
        refs = request.get("attachments", [])
        image = None
        if refs:
            if self.runtime.attachment_store is None:
                raise AttachmentError("attachment_unavailable", "附件原件不可用。")
            image = OutboundImage(
                self.runtime.attachment_store.verify(refs[0]["asset_id"]),
                refs[0]["display_name"],
            )

        def settle(result: OutboundSendResult) -> None:
            # These runtime preflight failures occur before the first provider send.
            # Partial sends and uncertain results remain terminal to avoid duplicates.
            retry = (
                result.outcome == "failed"
                and result.provider_message_id is None
                and result.error_kind in {"provider_unavailable", "runtime_stopped"}
            )
            self.store.update(
                did,
                im_state="pending" if retry else result.outcome,
                provider_receipt=result.provider_message_id,
                error=result.error_kind,
            )

        def claim() -> bool:
            with self.contact_lock:
                current = self.store.get(did)
                return (
                    self.contact_allowed(request["source_session_id"])
                    and datetime.now(UTC) < datetime.fromisoformat(current["expires_at"])
                    and self.store.claim_im(did)
                )

        deliver_once(
            [DeliveryAttempt(claim, settle)],
            lambda: im.send_proactive(
                request["destination_id"],
                request["content"],
                text_format="markdown",
                can_send=lambda: self.contact_allowed(request["source_session_id"]),
                image=image,
                prepared_event_uuid=row["message_id"],
            ),
        )

    def _report_failure(self, row: dict[str, Any]) -> None:
        if row["feedback_state"] != "pending" or self.runtime.agent_runtime is None:
            return
        request, did = row["request"], row["delivery_id"]
        source = request["source_session_id"]
        manager = self.runtime.persistent_session_manager
        if not manager.exists(source) or manager.is_archived(source) or manager.is_inner(source):
            # Inner activity already receives its receipts in the next wake snapshot.
            self.store.update(did, feedback_state="unavailable")
            return
        conversation = (
            "已写入目标会话" if row["conversation_state"] == "committed" else "未写入目标会话"
        )
        im_status = {
            "delivered": "IM 已发送",
            "unknown": "IM 发送结果未知，请先核实是否收到再决定重发",
            "not_requested": "未请求 IM 通知",
        }.get(row["im_state"], "IM 未发送")
        reason = {
            "delivery_expired": "等待投递已超过有效期",
            "binding_changed": "原来的通知绑定已变化",
            "interrupted_send": "发送过程中运行时中断，未取得确认",
            "send_timeout": "发送确认超时",
            "partial_delivery": "部分内容已发送，剩余内容未确认",
            "contact_paused": "主动联系已暂停",
        }.get(row["error"], row["error"] or "投递失败")
        content = (
            f"系统投递状态更新（{row['created_at']} 提交）\n\n"
            f"{conversation}；{im_status}。原因：{reason}。\n\n"
            f"原消息：{request['content'][:200]}"
        )
        feedback_id = f"{did}-feedback"
        try:
            with self.runtime.agent_runtime.try_reserve_session_mutation(source):
                manager.append_delivered_message(
                    source,
                    delivery_id=feedback_id,
                    content=content,
                    source_session_id=source,
                    source_run_id=request["source_run_id"],
                )
                if self.runtime.notification_service is not None:
                    self.runtime.notification_service.on_prepared_message(
                        source,
                        feedback_id,
                        content,
                        datetime.fromisoformat(row["created_at"]),
                    )
                self.store.update(did, feedback_state="delivered")
        except (SessionBusyError, SessionWriterConflictError):
            return
        except (SessionNotFoundError, AgentError):
            self.store.update(did, feedback_state="unavailable")
