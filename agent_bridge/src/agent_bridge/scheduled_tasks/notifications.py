"""Project a completed scheduled run through the existing IM delivery path."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent.scheduled_tasks import ScheduledOccurrence, ScheduledTaskStore
from agent.sessions.events import Event
from agent.sessions.query import query_session
from agent.sessions.store import SessionStore

if TYPE_CHECKING:
    from ..protocols.im.provider import OutboundSendResult
    from ..protocols.im.runtime import IMRuntime

logger = logging.getLogger(__name__)
_NOTIFIABLE = {"completed", "failed", "interrupted", "rejected"}
_STATUS = {
    "completed": "执行已结束",
    "failed": "执行失败",
    "interrupted": "执行中断",
    "rejected": "请求未能开始执行",
}


def final_run_text(events: Sequence[Event], run_id: str) -> str:
    """Read persisted final phases, never combine tool commentary into a result."""
    run = [event for event in events if event.meta.get("run_id") == run_id]
    phases = [event for event in run if event.type == "assistant_phase"]
    native = bool(phases and phases[-1].payload.get("mode") == "native")
    if native and phases[-1].payload.get("validation") != "valid":
        return ""
    # Tool responses before the final model response cannot become final text.
    boundary = max(
        (
            i
            for i, event in enumerate(run)
            if event.type in {"tool_call_request", "tool_call_result"}
        ),
        default=-1,
    )
    messages = [
        event
        for event in run[boundary + 1 :]
        if event.type == "assistant_message"
        and not event.payload.get("partial")
        and isinstance(event.payload.get("content"), str)
        and event.payload["content"].strip()
    ]
    finals = [event for event in messages if event.payload.get("phase") == "final_answer"]
    if finals:
        return "\n\n".join(str(event.payload["content"]) for event in finals)
    if (
        not native
        and len(messages) == 1
        and messages[0].payload.get("phase", "unknown") == "unknown"
    ):
        return str(messages[0].payload["content"])
    return ""


class ScheduledIMNotifications:
    def __init__(
        self, store: ScheduledTaskStore, sessions: SessionStore, im_runtime: IMRuntime | None
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._im = im_runtime
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run, name="ScheduledIMNotifications", daemon=True
        )
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float | None = None) -> bool:
        # The app awaits this off its event loop before stopping IM. The current
        # send has its own bounded timeout; never orphan a worker during teardown.
        self._stopping.set()
        self._wake.set()
        if self._thread is None:
            return True
        self._thread.join(timeout)
        stopped = not self._thread.is_alive()
        if stopped:
            self._thread = None
        return stopped

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.clear()
            try:
                self.deliver_pending()
            except Exception:
                logger.exception("Scheduled notification batch failed")
            self._wake.wait(1.0)

    def deliver_pending(self) -> None:
        for occurrence_id in self._store.notifications.pending_ids():
            if self._stopping.is_set():
                return
            try:
                self._deliver(self._store.get_occurrence(occurrence_id))
            except Exception:
                # Claimed targets remain unknown. Only unattempted targets can be revisited.
                logger.exception("Scheduled notification failed occurrence=%s", occurrence_id)

    def _deliver(self, occurrence: ScheduledOccurrence) -> None:
        ledger = self._store.notifications
        outcome = occurrence.outcome.value if occurrence.outcome else ""
        if outcome not in _NOTIFIABLE:
            ledger.close_unprepared(occurrence.id, "outcome_not_notified")
            return
        if ledger.summary(occurrence.id)["state"] == "pending":
            if self._im is None:
                ledger.close_unprepared(occurrence.id, "im_runtime_unavailable", failed=True)
                return
            session_id = occurrence.session_id or occurrence.task_snapshot.target_session_id
            targets = tuple(
                item.destination_id
                for item in self._im.presence_snapshots()
                if item.session_id == session_id
            )
            if not targets:
                ledger.close_unprepared(occurrence.id, "no_binding")
                return
            text = ""
            if occurrence.run_id and session_id:
                try:
                    snapshot = query_session(
                        self._sessions, session_id, cancel_check=self._check_stop
                    )
                    text = final_run_text(snapshot.active_events, occurrence.run_id)
                except Exception:
                    if self._stopping.is_set():
                        return
                    if outcome == "completed":
                        ledger.close_unprepared(occurrence.id, "result_unavailable", failed=True)
                        return
                    # A rejected request may have no persisted session/run at all.
                    # Its known terminal status is still sufficient for a failure notice.
            if not text:
                text = "请在原会话查看执行记录。"
            ledger.prepare(
                occurrence.id,
                f"定时任务：{occurrence.task_snapshot.name}\n{_STATUS[outcome]}\n\n{text}",
                targets,
            )
        for delivery in ledger.summary(occurrence.id)["deliveries"]:
            if self._stopping.is_set():
                return
            if delivery["state"] != "pending":
                continue
            self._send(occurrence.id, str(delivery["destination_id"]))
        ledger.finish(occurrence.id)

    def _send(self, occurrence_id: str, destination_id: str) -> None:
        from ..protocols.im.delivery.once import DeliveryAttempt, deliver_once
        from ..protocols.im.provider import OutboundSendResult

        ledger = self._store.notifications

        def settle(result: OutboundSendResult) -> None:
            ledger.settle(
                occurrence_id,
                destination_id,
                outcome=result.outcome,
                receipt=result.provider_message_id,
                error_kind=result.error_kind,
            )

        def send() -> OutboundSendResult:
            if self._im is None:
                return OutboundSendResult("failed", error_kind="im_runtime_unavailable")
            return self._im.send_proactive(
                destination_id, ledger.text(occurrence_id), text_format="markdown"
            )

        deliver_once(
            [DeliveryAttempt(lambda: ledger.claim(occurrence_id, destination_id), settle)], send
        )

    def _check_stop(self) -> None:
        if self._stopping.is_set():
            raise RuntimeError("notification_worker_stopping")
