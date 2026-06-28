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

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from .contracts import DEFAULT_OWNER_USER_ID, MemoryContext, RecallTrace, RecallTraceItem
from .extraction import AsyncExtractionWorker
from .retrieval import Renderer, ScoreWeights, pinned_gate, rank

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .contracts import ConversationFragment, GateMode
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
        on_retrieved: 召回完成回调（observability / inspector）。
        owner_user_id: 多 user 预留维度，v1 固定。
        top_k: 召回注入条目上限。
        pinned_relevance_gate: 是否启用 pinned relevance gate（pass-1 M13.3，
            issue 003 主因 2）。``True``（默认）→ 用 :func:`pinned_gate` 按 query
            相关性过滤 pinned；``False`` → pre-pass-1 行为（pinned 全量注入）。
            切片 baseline 关此开关用作对照。
        pinned_gate_mode: gate 阈值档位（``strict`` / ``lenient``，详见
            :data:`memory.contracts.GateMode`）。仅 ``pinned_relevance_gate=True``
            时生效。
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
        on_retrieved: Callable[[RecallTrace], None] | None = None,
        owner_user_id: str = DEFAULT_OWNER_USER_ID,
        top_k: int = _RETRIEVE_TOP_K,
        pinned_relevance_gate: bool = True,
        pinned_gate_mode: GateMode = "lenient",
    ) -> None:
        self._store = store
        self._retrieval = retrieval
        self._renderer = renderer or Renderer()
        self._weights = weights or ScoreWeights()
        self._owner = owner_user_id
        self._top_k = top_k
        self._pinned_gate_enabled = pinned_relevance_gate
        self._pinned_gate_mode: GateMode = pinned_gate_mode
        self._on_retrieved = on_retrieved
        self._worker = AsyncExtractionWorker(
            store, extractor, reconciler, on_extracted=on_extracted
        )

    @property
    def store(self) -> SqliteMemoryStore:
        """只读暴露底层 store，供 inspector 等 dev 工具访问（非公共 API）。"""
        return self._store

    # ----- 写路径 -----

    def observe(self, fragment: ConversationFragment) -> None:
        """提交一段对话素材做异步抽取（非阻塞）。"""
        logger.info(
            "observe owner=%s persona=%s utterances=%d",
            fragment.owner_user_id,
            fragment.persona_id,
            len(fragment.utterances),
        )
        self._worker.submit(fragment)

    def invalidate_sources(
        self,
        *,
        session_id: str,
        event_uuids: set[str],
        reason: str,
    ) -> dict[str, int]:
        """让指定会话事件来源的记忆失活。

        先 drain 异步抽取队列，保证已入队的旧分支 fragment 不会在软删除之后再落
        新记忆；随后按 source_ref / provenance 软删除 episodic 与 semantic。
        """
        if not event_uuids:
            return {"episodic": 0, "semantic": 0}

        logger.info(
            "invalidate_sources session=%s events=%d reason=%s",
            session_id,
            len(event_uuids),
            reason,
        )
        self.flush()
        return self._store.soft_delete_by_source_events(
            session_id=session_id,
            event_uuids=set(event_uuids),
            deleted_at=datetime.now(UTC),
        )

    def warmup(self) -> None:
        """Warm read-path dependencies without changing memory semantics."""
        logger.info("warmup owner=%s", self._owner)
        self._store.warmup()

    # ----- 读路径 -----

    def retrieve(
        self,
        query: str,
        *,
        persona_id: str,
        session_id: str | None = None,
        owner_user_id: str | None = None,
        top_k: int | None = None,
        source: Literal["natural", "probe"] = "natural",
    ) -> MemoryContext:
        """召回与 ``query`` 相关的记忆，渲染成可注入的 :class:`MemoryContext`。

        空召回（含无 pinned 无命中）返回 :meth:`MemoryContext.empty`，调用方据
        :meth:`MemoryContext.is_empty` 决定是否注入（如实反馈，R-4.5.3）。
        """
        owner = owner_user_id or self._owner
        effective_top_k = top_k if top_k is not None else self._top_k
        now = datetime.now(UTC)

        logger.info(
            "retrieve owner=%s persona=%s query_len=%d top_k=%d source=%s",
            owner,
            persona_id,
            len(query),
            effective_top_k,
            source,
        )

        pinned_raw = self._store.pinned(owner_user_id=owner, persona_id=persona_id)
        pinned_pre = len(pinned_raw)

        if self._pinned_gate_enabled:
            pinned = pinned_gate(
                query,
                pinned_raw,
                store=self._store,
                owner_user_id=owner,
                mode=self._pinned_gate_mode,
            )
            stripped = query.strip()
            if not stripped or (self._pinned_gate_mode == "lenient" and len(stripped) < 6):
                gate_decision: Literal["disabled", "pass-through", "matched"] = "pass-through"
            else:
                gate_decision = "matched"
        else:
            pinned = pinned_raw
            gate_decision = "disabled"

        recalled: list[tuple[Candidate, float]] = []
        candidates_count = 0
        if self._retrieval is not None and query.strip():
            cands = self._retrieval.search(
                query, owner_user_id=owner, persona_id=persona_id, limit=effective_top_k
            )
            candidates_count = len(cands)
            recalled = rank(cands, now=now, top_k=effective_top_k, weights=self._weights)
            logger.info(
                "retrieve recalled pinned=%d candidates=%d ranked=%d",
                len(pinned),
                candidates_count,
                len(recalled),
            )
        else:
            logger.info("retrieve skipped ranking (no strategy or empty query)")

        ctx = self._renderer.render(pinned=pinned, recalled=recalled, now=now)

        if self._on_retrieved is not None:
            trace = RecallTrace(
                timestamp=now,
                query=query,
                owner_user_id=owner,
                persona_id=persona_id,
                top_k=effective_top_k,
                source=source,
                pinned_pre_gate=pinned_pre,
                pinned_post_gate=len(pinned),
                gate_enabled=self._pinned_gate_enabled,
                gate_mode=self._pinned_gate_mode if self._pinned_gate_enabled else None,
                gate_decision=gate_decision,
                candidates_count=candidates_count,
                ranked_count=len(recalled),
                items=[
                    RecallTraceItem(
                        text=item.text,
                        layer=item.layer,
                        source_ref=item.source_ref,
                        score=item.score,
                    )
                    for item in ctx.items
                ],
            )
            try:
                self._on_retrieved(trace)
            except Exception:
                logger.exception("on_retrieved callback failed; dropping trace")

        return ctx

    # ----- 生命周期 -----

    def flush(self) -> None:
        """阻塞直到抽取队列清空。"""
        self._worker.flush()

    def close(self) -> None:
        """drain 抽取队列、停 worker、关库。幂等。"""
        self._worker.close()
        self._store.close()
