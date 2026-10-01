"""Serial extraction with durable deferral and activity-triggered bounded recovery."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..contracts import ConversationFragment
    from ..store import SqliteMemoryStore
    from .extractor import Extractor

from .context import ExtractionContextBuilder
from .deferred import DeferredObservationError, decode, encode, observation_id
from .reconciler import ExtractionApplyError, Reconciler
from .result import ExtractionResult

__all__ = ["AsyncExtractionWorker"]

logger = logging.getLogger(__name__)

_SENTINEL = object()
"""投入队列触发 worker 退出的哨兵。"""


class AsyncExtractionWorker:
    """后台线程消费 fragment 队列做抽取落库。

    Args:
        store: 记忆库。
        extractor: LLM 抽取器。
        reconciler: 落库组件。
        on_extracted: 可选回调，每完成一次有效落库回报 :class:`ExtractionResult`
            （observability，design §8）。回调异常会被吞掉只记日志。
    """

    def __init__(
        self,
        store: SqliteMemoryStore,
        extractor: Extractor,
        reconciler: Reconciler,
        *,
        on_extracted: Callable[[ExtractionResult], None] | None = None,
        clock: Callable[[], float] = time.time,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._store = store
        self._extractor = extractor
        self._reconciler = reconciler
        self._context_builder = ExtractionContextBuilder(store)
        self._on_extracted = on_extracted
        self._queue: queue.Queue[object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._clock = clock
        self._cooldown_seconds = cooldown_seconds
        self._processing = threading.RLock()
        self._degraded = False
        self._retry_after = 0.0
        self._blocked_code: str | None = None

    def submit(self, fragment: ConversationFragment) -> None:
        """Persist material; only fresh non-failed activity can permit a bounded drain."""
        with self._lock:
            if self._closed:
                return
            if not fragment.is_empty():
                refs = [u.source_ref for u in fragment.utterances]
                if fragment.semantic_update_context:
                    refs.extend(fragment.semantic_update_context.source_refs)
                added = self._store.enqueue_extraction(
                    observation_id(fragment),
                    fragment.session_id,
                    encode(fragment),
                    refs,
                    "turn_failed" if fragment.defer_extraction else "ready",
                )
                logger.info(
                    "memory_observation action=%s reason=%s",
                    "queued" if added else "duplicate",
                    "turn_failed" if fragment.defer_extraction else "activity",
                )
            if fragment.defer_extraction:
                return
            self._ensure_started()
            # A queued activity must never release material from a later failed turn.
            self._queue.put(self._store.pending_extraction_sequence())

    def invalidate(self, session_id: str, event_uuids: set[str] | None = None) -> int:
        """Synchronize with the active extraction; never drain deferred work here."""
        with self._processing:
            return self._store.invalidate_pending_extractions(session_id, event_uuids)

    def flush(self) -> None:
        """等待已触发的处理；不主动释放延后素材。"""
        self._queue.join()

    def close(self) -> None:
        """等待当前处理并停线程；未处理素材保留在库中。幂等。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread is None:
            return
        self._queue.join()
        self._queue.put(_SENTINEL)
        thread.join()

    # ----- 内部 -----

    def _ensure_started(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="memory-extraction", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                with self._processing:
                    assert isinstance(item, int)
                    self._drain(item)
            except Exception:
                self._store.defer_extractions(
                    self._clock() + self._cooldown_seconds, "worker_failed"
                )
                logger.warning("记忆抽取处理一条 fragment 失败", exc_info=True)
            finally:
                self._queue.task_done()

    def _drain(self, through: int) -> None:
        if self._closed:
            return
        now = self._clock()
        if now < self._store.extraction_cooldown():
            logger.info("memory_observation action=deferred reason=cooldown")
            return
        pending = self._store.pending_extractions(limit=2, through=through)
        recovery = any(reason != "ready" for _, _, reason in pending)
        processed = 0
        for identity, payload, _ in pending:
            if self._closed:
                break
            self._degraded = False
            self._retry_after = 0.0
            self._blocked_code = None
            try:
                fragment = decode(payload)
                if observation_id(fragment) != identity:
                    raise DeferredObservationError("observation identity mismatch")
            except DeferredObservationError:
                self._store.block_pending_extraction(identity, "invalid_deferred_observation")
                logger.warning(
                    "memory_observation action=blocked reason=invalid_deferred_observation"
                )
                continue
            succeeded = self._process(fragment, recovery=recovery)
            if self._blocked_code is not None:
                self._store.block_pending_extraction(identity, self._blocked_code)
                if self._degraded:
                    self._store.defer_extractions(
                        self._clock() + max(self._cooldown_seconds, self._retry_after),
                        "coordination_failed",
                    )
                    break
                continue
            if not succeeded:
                self._store.defer_extractions(
                    self._clock() + max(self._cooldown_seconds, self._retry_after),
                    "extraction_failed",
                )
                logger.info(
                    "memory_observation action=deferred reason=extraction_failed processed=%d",
                    processed,
                )
                return
            self._store.finish_pending_extraction(identity)
            processed += 1
            if self._degraded:
                self._store.defer_extractions(
                    self._clock() + max(self._cooldown_seconds, self._retry_after),
                    "coordination_failed",
                )
                break
        if recovery and not self._degraded:
            self._store.defer_extractions(
                self._clock() + self._cooldown_seconds, "recovery_cooldown"
            )
        logger.info("memory_observation action=processed count=%d recovery=%s", processed, recovery)

    def _process(self, fragment: ConversationFragment, *, recovery: bool = False) -> bool:
        if fragment.is_empty():
            return True
        context = self._context_builder.build(fragment)
        output = self._extractor.extract(fragment, context, allow_retries=not recovery)
        self._degraded = output.reconciliation_degraded
        if output.retry_after_seconds is not None and math.isfinite(output.retry_after_seconds):
            self._retry_after = max(0.0, output.retry_after_seconds)
        if output.failure_code is not None:
            if self._on_extracted is not None:
                try:
                    self._on_extracted(
                        ExtractionResult(
                            session_id=fragment.session_id,
                            committed=False,
                            error_code=output.failure_code,
                        )
                    )
                except Exception:
                    logger.warning("on_extracted 回调异常", exc_info=True)
            return False
        # Even a legal empty proposal is committed: observation identity is the
        # idempotency boundary, not whether the model happened to emit writes.
        try:
            result = self._reconciler.apply(output, fragment, context)
        except ExtractionApplyError as exc:
            if exc.code == "observation_time_conflict":
                self._blocked_code = exc.code
            logger.warning("extraction batch rejected code=%s detail=%s", exc.code, exc)
            result = ExtractionResult(
                session_id=fragment.session_id,
                committed=False,
                error_code=exc.code,
                error_message=str(exc),
            )
        if self._on_extracted is not None:
            try:
                self._on_extracted(result)
            except Exception:
                logger.warning("on_extracted 回调异常", exc_info=True)

        return result.committed or result.duplicate
