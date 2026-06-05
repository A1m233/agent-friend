"""``AsyncExtractionWorker``：单 worker + FIFO 队列的异步抽取。

设计要点（design §5.1）：

- ``observe`` → ``submit`` **非阻塞入队**，对话主线程零额外延迟。
- **单线程串行**：保证 reconcile 时"看到的旧事实"状态一致，不并发打架。
- ``flush`` / ``close`` 在退出前 drain，把没抽完的处理掉。
- 抽取是旁路：单条处理出错只记日志、不影响后续，不让线程崩。
- **崩溃语义**：``kill -9`` 时队列里未处理的 fragment 丢失（v1 接受，future
  由 backfill 兜）。
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..contracts import ConversationFragment
    from ..store import SqliteMemoryStore
    from .extractor import Extractor
    from .reconciler import Reconciler
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
    ) -> None:
        self._store = store
        self._extractor = extractor
        self._reconciler = reconciler
        self._on_extracted = on_extracted
        self._queue: queue.Queue[object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, fragment: ConversationFragment) -> None:
        """非阻塞入队。``close`` 之后调用会被忽略（只记日志）。"""
        with self._lock:
            if self._closed:
                logger.warning("worker 已关闭，丢弃 observe 的 fragment")
                return
            self._ensure_started()
            self._queue.put(fragment)

    def flush(self) -> None:
        """阻塞直到队列中已入队的 fragment 全部处理完。"""
        self._queue.join()

    def close(self) -> None:
        """drain + 停线程。幂等。"""
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
                self._process(item)  # type: ignore[arg-type]
            except Exception:
                logger.warning("记忆抽取处理一条 fragment 失败", exc_info=True)
            finally:
                self._queue.task_done()

    def _process(self, fragment: ConversationFragment) -> None:
        if fragment.is_empty():
            return
        existing = self._store.related_semantic(
            owner_user_id=fragment.owner_user_id, persona_id=fragment.persona_id
        )
        output = self._extractor.extract(fragment, existing)
        if output.is_noop():
            return
        result = self._reconciler.apply(output, fragment, existing)
        if self._on_extracted is not None and not result.is_empty():
            try:
                self._on_extracted(result)
            except Exception:
                logger.warning("on_extracted 回调异常", exc_info=True)
