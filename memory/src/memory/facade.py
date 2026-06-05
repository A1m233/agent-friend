"""``Memory``：记忆系统的对外门面（读 ``retrieve`` / 写 ``observe``）。

编排读写两条路，对调用方（``agent.Conversation``）隐藏内部结构：

- ``observe(fragment)``：非阻塞入队，异步抽取（写路径，design §5）。
- ``retrieve(query, ...)``：pinned 全量 + 关键词召回打分 → 渲染成 :class:`MemoryContext`
  （读路径，design §6）。
- ``flush()`` / ``close()``：退出前 drain 抽取队列（design §5.1）。

同步/异步、关键词/向量等都是内部实现，``observe`` / ``retrieve`` 契约稳定
（design §10.1）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .contracts import DEFAULT_OWNER_USER_ID, MemoryContext
from .extraction import AsyncExtractionWorker
from .retrieval import Renderer, ScoreWeights, rank

if TYPE_CHECKING:
    from collections.abc import Callable

    from .contracts import ConversationFragment
    from .extraction import ExtractionResult, Extractor, Reconciler
    from .retrieval import Candidate, RetrievalStrategy
    from .store import SqliteMemoryStore

__all__ = ["Memory"]

_RETRIEVE_TOP_K = 8
"""单次召回（pinned 之外）注入的条目上限。"""


class Memory:
    """记忆系统门面。

    Args:
        store: 记忆库。
        extractor: LLM 抽取器（写路径）。
        reconciler: 落库组件（写路径）。
        retrieval: 召回策略（读路径）；``None`` 时 :meth:`retrieve` 只返回 pinned。
        renderer: 渲染器；默认 :class:`Renderer`。
        weights: 打分权重；默认 :class:`ScoreWeights`。
        on_extracted: 抽取完成回调（observability）。
        owner_user_id: 多 user 预留维度，v1 固定。
        top_k: 召回注入条目上限。
    """

    def __init__(
        self,
        store: SqliteMemoryStore,
        extractor: Extractor,
        reconciler: Reconciler,
        *,
        retrieval: RetrievalStrategy | None = None,
        renderer: Renderer | None = None,
        weights: ScoreWeights | None = None,
        on_extracted: Callable[[ExtractionResult], None] | None = None,
        owner_user_id: str = DEFAULT_OWNER_USER_ID,
        top_k: int = _RETRIEVE_TOP_K,
    ) -> None:
        self._store = store
        self._retrieval = retrieval
        self._renderer = renderer or Renderer()
        self._weights = weights or ScoreWeights()
        self._owner = owner_user_id
        self._top_k = top_k
        self._worker = AsyncExtractionWorker(
            store, extractor, reconciler, on_extracted=on_extracted
        )

    # ----- 写路径 -----

    def observe(self, fragment: ConversationFragment) -> None:
        """提交一段对话素材做异步抽取（非阻塞）。"""
        self._worker.submit(fragment)

    # ----- 读路径 -----

    def retrieve(
        self,
        query: str,
        *,
        persona_id: str,
        session_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> MemoryContext:
        """召回与 ``query`` 相关的记忆，渲染成可注入的 :class:`MemoryContext`。

        空召回（含无 pinned 无命中）返回 :meth:`MemoryContext.empty`，调用方据
        :meth:`MemoryContext.is_empty` 决定是否注入（如实反馈，R-4.5.3）。
        """
        owner = owner_user_id or self._owner
        now = datetime.now(UTC)

        pinned = self._store.pinned(owner_user_id=owner, persona_id=persona_id)

        recalled: list[tuple[Candidate, float]] = []
        if self._retrieval is not None and query.strip():
            cands = self._retrieval.search(
                query, owner_user_id=owner, persona_id=persona_id, limit=self._top_k
            )
            recalled = rank(cands, now=now, top_k=self._top_k, weights=self._weights)

        return self._renderer.render(pinned=pinned, recalled=recalled, now=now)

    # ----- 生命周期 -----

    def flush(self) -> None:
        """阻塞直到抽取队列清空。"""
        self._worker.flush()

    def close(self) -> None:
        """drain 抽取队列、停 worker、关库。幂等。"""
        self._worker.close()
        self._store.close()
