"""Per-session mailbox + 有界 worker pool 调度器。"""

from __future__ import annotations

import heapq
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count

from .cancellation import CancelReason, RunCancelled
from .models import RunHandle, RunOutcome, RunPriority, RunRequest, RunStatus

logger = logging.getLogger(__name__)

RunExecutor = Callable[[RunRequest, RunHandle], None]


@dataclass(frozen=True)
class RuntimeActivitySnapshot:
    accepting: bool
    queued: int
    running: int


@dataclass(frozen=True)
class RuntimeDrainReport:
    queued_cancelled: int
    running_at_start: int
    completed_during_grace: int
    cancellation_requested: int
    still_running: int
    timed_out: bool


class RunScheduler:
    """同 session 串行、跨 session 有界并发的进程内调度器。"""

    def __init__(
        self,
        execute: RunExecutor,
        *,
        max_workers: int = 4,
        max_queued_runs: int = 256,
        max_session_queue: int = 32,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        if max_workers <= 0 or max_queued_runs <= 0 or max_session_queue <= 0:
            raise ValueError("scheduler limits must be positive")
        if max_session_queue > max_queued_runs:
            raise ValueError("max_session_queue cannot exceed max_queued_runs")
        self._execute = execute
        self._max_workers = max_workers
        self._max_queued_runs = max_queued_runs
        self._max_session_queue = max_session_queue
        self._now = now_factory or (lambda: datetime.now(UTC))
        self._condition = threading.Condition()
        self._mailboxes: dict[str, list[tuple[int, int, str]]] = {}
        self._handles: dict[str, RunHandle] = {}
        self._requests: dict[str, RunRequest] = {}
        self._dedupe: dict[tuple[str, str], str] = {}
        self._active: dict[str, str] = {}
        self._ready: list[tuple[int, int, str, int]] = []
        self._generation: dict[str, int] = {}
        self._seq = count()
        self._ready_seq = count()
        self._queued_count = 0
        self._accepting = False
        self._stop = False
        self._dispatcher: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None

    def start(self) -> None:
        with self._condition:
            if self._dispatcher is not None:
                return
            self._accepting = True
            self._stop = False
            self._pool = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="AgentRun",
            )
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                daemon=True,
                name="AgentRunScheduler",
            )
            self._dispatcher.start()

    def submit(self, request: RunRequest) -> RunHandle:
        handle = RunHandle(
            request,
            primary_consumer=not bool(request.metadata.get("detached", False)),
        )
        with self._condition:
            if not self._accepting:
                handle.finish_once(RunOutcome.REJECTED, reason_code="runtime_not_accepting")
                return handle
            if request.run_id in self._handles:
                handle.finish_once(RunOutcome.REJECTED, reason_code="duplicate_run_id")
                return handle
            if request.expires_at is not None and request.expires_at <= self._now():
                handle.finish_once(RunOutcome.EXPIRED, reason_code="expired_before_enqueue")
                return handle

            mailbox = self._mailboxes.setdefault(request.session_id, [])
            # interactive 先清理同 session 的低优先级排队再做容量判断；否则
            # mailbox 被主动事件占满时，真正的用户输入会被错误拒绝。
            if request.priority is RunPriority.INTERACTIVE:
                self._supersede_lower_priority_locked(request.session_id)
                self._mailbox_head_locked(request.session_id)
            if (
                self._queued_count >= self._max_queued_runs
                or len(mailbox) >= self._max_session_queue
            ):
                outcome = (
                    RunOutcome.REJECTED
                    if request.priority is RunPriority.INTERACTIVE
                    else RunOutcome.SKIPPED
                )
                handle.finish_once(outcome, reason_code="queue_full")
                return handle

            self._handles[request.run_id] = handle
            self._requests[request.run_id] = request
            if request.dedupe_key is not None:
                key = (request.session_id, request.dedupe_key)
                old_id = self._dedupe.get(key)
                if old_id is not None:
                    old = self._handles.get(old_id)
                    if old is not None and old.status is RunStatus.QUEUED:
                        old.cancel(CancelReason.DEDUPLICATED)
                        old.finish_once(RunOutcome.SKIPPED, reason_code="deduplicated")
                        self._queued_count -= 1
                self._dedupe[key] = request.run_id

            heapq.heappush(
                mailbox,
                (int(request.priority), next(self._seq), request.run_id),
            )
            self._queued_count += 1
            self._refresh_ready_locked(request.session_id)
            self._condition.notify_all()
        return handle

    def activity_snapshot(self) -> RuntimeActivitySnapshot:
        with self._condition:
            return RuntimeActivitySnapshot(
                accepting=self._accepting,
                queued=self._queued_count,
                running=len(self._active),
            )

    def quiesce(self) -> RuntimeActivitySnapshot:
        """Stop accepting work without cancelling anything already admitted."""
        with self._condition:
            self._accepting = False
            return RuntimeActivitySnapshot(
                accepting=False,
                queued=self._queued_count,
                running=len(self._active),
            )

    def drain(
        self,
        *,
        grace_timeout: float = 5.0,
        cancel_timeout: float = 5.0,
    ) -> RuntimeDrainReport:
        """Quiesce, cancel queued work, then cooperatively cancel overdue workers."""
        if grace_timeout < 0 or cancel_timeout < 0:
            raise ValueError("drain timeouts must be non-negative")

        with self._condition:
            self._accepting = False
            queued_cancelled = 0
            for handle in self._handles.values():
                if handle.status is RunStatus.QUEUED:
                    handle.cancel(CancelReason.RUNTIME_STOPPING)
                    if handle.finish_once(
                        RunOutcome.CANCELLED,
                        reason_code="runtime_draining",
                    ):
                        queued_cancelled += 1
                        self._queued_count -= 1
            running_at_start = len(self._active)
            self._condition.notify_all()

            grace_deadline = time.monotonic() + grace_timeout
            while self._active:
                remaining = grace_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)

            completed_during_grace = running_at_start - len(self._active)
            cancellation_requested = 0
            for run_id in tuple(self._active.values()):
                handle = self._handles[run_id]
                if handle.cancel(CancelReason.RUNTIME_STOPPING):
                    cancellation_requested += 1

            cancel_deadline = time.monotonic() + cancel_timeout
            while self._active:
                remaining = cancel_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)

            still_running = len(self._active)
            return RuntimeDrainReport(
                queued_cancelled=queued_cancelled,
                running_at_start=running_at_start,
                completed_during_grace=completed_during_grace,
                cancellation_requested=cancellation_requested,
                still_running=still_running,
                timed_out=still_running > 0,
            )

    def stop(self, timeout: float = 5.0) -> bool:
        with self._condition:
            if self._dispatcher is None:
                return True
            self._accepting = False
            self._stop = True
            for handle in self._handles.values():
                if handle.status is RunStatus.QUEUED:
                    handle.cancel(CancelReason.RUNTIME_STOPPING)
                    handle.finish_once(RunOutcome.CANCELLED, reason_code="runtime_stopping")
                elif handle.status is RunStatus.RUNNING:
                    handle.cancel(CancelReason.RUNTIME_STOPPING)
            self._condition.notify_all()
            dispatcher = self._dispatcher
            pool = self._pool
        dispatcher.join(timeout=timeout)
        clean = not dispatcher.is_alive()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        with self._condition:
            if clean and not self._active:
                self._dispatcher = None
                self._pool = None
            else:
                clean = False
        return clean

    def _supersede_lower_priority_locked(self, session_id: str) -> None:
        active_id = self._active.get(session_id)
        if active_id is not None:
            active_request = self._requests[active_id]
            if active_request.priority > RunPriority.INTERACTIVE:
                self._handles[active_id].cancel(CancelReason.SUPERSEDED_BY_USER)
        for _, _, run_id in self._mailboxes.get(session_id, []):
            request = self._requests.get(run_id)
            handle = self._handles.get(run_id)
            if (
                request is not None
                and handle is not None
                and request.priority > RunPriority.INTERACTIVE
                and handle.status is RunStatus.QUEUED
            ):
                handle.cancel(CancelReason.SUPERSEDED_BY_USER)
                handle.finish_once(RunOutcome.CANCELLED, reason_code="superseded_by_user")
                self._queued_count -= 1

    def _mailbox_head_locked(self, session_id: str) -> tuple[int, int, str] | None:
        mailbox = self._mailboxes.get(session_id)
        if mailbox is None:
            return None
        while mailbox:
            item = mailbox[0]
            handle = self._handles[item[2]]
            if handle.status is RunStatus.QUEUED:
                return item
            heapq.heappop(mailbox)
        return None

    def _refresh_ready_locked(self, session_id: str) -> None:
        if session_id in self._active:
            return
        head = self._mailbox_head_locked(session_id)
        if head is None:
            return
        generation = self._generation.get(session_id, 0) + 1
        self._generation[session_id] = generation
        heapq.heappush(
            self._ready,
            (head[0], next(self._ready_seq), session_id, generation),
        )

    def _next_locked(self) -> tuple[RunRequest, RunHandle] | None:
        while self._ready:
            _, _, session_id, generation = heapq.heappop(self._ready)
            if self._generation.get(session_id) != generation or session_id in self._active:
                continue
            head = self._mailbox_head_locked(session_id)
            if head is None:
                continue
            _, _, run_id = heapq.heappop(self._mailboxes[session_id])
            handle = self._handles[run_id]
            request = self._requests[run_id]
            if request.expires_at is not None and request.expires_at <= self._now():
                self._queued_count -= 1
                handle.finish_once(RunOutcome.EXPIRED, reason_code="expired_before_start")
                self._refresh_ready_locked(session_id)
                continue
            if not handle.mark_running():
                self._refresh_ready_locked(session_id)
                continue
            self._queued_count -= 1
            self._active[session_id] = run_id
            if request.dedupe_key is not None:
                self._dedupe.pop((session_id, request.dedupe_key), None)
            return request, handle
        return None

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                while True:
                    if self._stop and not self._active:
                        return
                    next_run = self._next_locked()
                    if next_run is not None:
                        break
                    self._condition.wait()
                pool = self._pool
            if pool is None:  # pragma: no cover - defensive shutdown race
                return
            request, handle = next_run
            pool.submit(self._run_one, request, handle)

    def _run_one(self, request: RunRequest, handle: RunHandle) -> None:
        try:
            handle.token.raise_if_cancelled()
            self._execute(request, handle)
            handle.token.raise_if_cancelled()
            handle.finish_once(RunOutcome.COMPLETED)
        except RunCancelled as exc:
            handle.finish_once(
                RunOutcome.CANCELLED,
                reason_code=str(exc.cancellation.reason),
                error=exc,
            )
        except BaseException as exc:
            logger.exception("run executor failed: %s", request.run_id)
            handle.finish_once(RunOutcome.FAILED, reason_code="executor_error", error=exc)
        finally:
            with self._condition:
                self._active.pop(request.session_id, None)
                self._refresh_ready_locked(request.session_id)
                self._condition.notify_all()
