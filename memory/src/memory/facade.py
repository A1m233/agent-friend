"""``Memory``：记忆系统的对外门面（读 ``retrieve`` / 写 ``observe``）。

编排读写两条路，对调用方（``agent.Conversation``）隐藏内部结构：

- ``observe(fragment)``：非阻塞入队，异步抽取（写路径，design §5）。
- ``retrieve(query, ...)``：自然对话时全量 Core Profile + 普通记忆召回；probe 只返回
  普通记忆（读路径，084）。
- ``flush()`` / ``close()``：退出前 drain 抽取队列（design §5.1）。

同步/异步、关键词/向量等都是内部实现，``observe`` / ``retrieve`` 契约稳定
（design §10.1）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from .contracts import (
    DEFAULT_OWNER_USER_ID,
    MemoryContext,
    MemoryItem,
    RecallTrace,
    RecallTraceItem,
)
from .core_profile import CoreProfileError, CoreProfileRenderer
from .extraction import AsyncExtractionWorker
from .retrieval import Renderer, ScoreWeights, rank
from .search import CurrentSessionRecallScope, MemorySearchService, SearchRequest
from .search.execution import CancelCheck, check_cancel
from .store import SemanticRow

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .contracts import ConversationFragment, GateMode
    from .extraction import ExtractionResult, Extractor, Reconciler
    from .retrieval import Candidate, RetrievalStrategy
    from .search import DenseIndexState, EmbeddingIndexManager
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
        pinned_relevance_gate: 旧评测配置兼容字段；v5 生产路径不再读取 pinned。
        pinned_gate_mode: 旧 trace/replay 兼容字段；v5 生产路径不再执行 gate。
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
        search_service: MemorySearchService | None = None,
        index_manager: EmbeddingIndexManager | None = None,
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
        self._search_service = search_service
        self._index_manager = index_manager

        def _extracted(result: ExtractionResult) -> None:
            if self._index_manager is not None and result.committed and not result.is_empty():
                self._index_manager.request_backfill()
            if on_extracted is not None:
                on_extracted(result)

        self._worker = AsyncExtractionWorker(
            store,
            extractor,
            reconciler,
            on_extracted=_extracted if index_manager is not None else on_extracted,
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
        result = self._store.soft_delete_by_source_events(
            session_id=session_id,
            event_uuids=set(event_uuids),
            deleted_at=datetime.now(UTC),
        )
        if self._index_manager is not None:
            self._index_manager.request_backfill()
        return result

    def warmup(self) -> None:
        """Warm read-path dependencies without changing memory semantics."""
        logger.info("warmup owner=%s", self._owner)
        self._store.warmup()
        if self._index_manager is not None:
            self._index_manager.start()

    def rebuild_dense_index(self) -> DenseIndexState | None:
        """同步刷新派生向量索引，供评测 / 维护入口建立确定性读取前置条件。

        生产读路径仍使用后台回填；这个显式入口不会被普通 ``retrieve`` 自动调用。
        没有装配 dense 组件时返回 ``None``，调用方可据此 fail closed。
        """
        self.flush()
        if self._index_manager is None:
            return None
        return self._index_manager.backfill_once()

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
        automatic_recall_scope: CurrentSessionRecallScope | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> MemoryContext:
        """召回与 ``query`` 相关的记忆，渲染成可注入的 :class:`MemoryContext`。

        空召回（含无 pinned 无命中）返回 :meth:`MemoryContext.empty`，调用方据
        :meth:`MemoryContext.is_empty` 决定是否注入（如实反馈，R-4.5.3）。
        """
        check_cancel(cancel_check)
        owner = owner_user_id or self._owner
        if source == "probe" and automatic_recall_scope is not None:
            raise ValueError("probe retrieve 不接受 automatic_recall_scope")
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

        if self._search_service is not None and self._pinned_gate_enabled:
            return self._retrieve_with_search(
                query,
                persona_id=persona_id,
                session_id=session_id,
                owner=owner,
                top_k=effective_top_k,
                source=source,
                now=now,
                automatic_recall_scope=automatic_recall_scope,
                cancel_check=cancel_check,
            )

        # v5 no longer has a pinned Semantic tier. Core is a separate, complete
        # profile and is only injected for natural conversation retrieval.
        pinned: list[SemanticRow] = []
        pinned_pre = 0
        gate_decision: Literal["disabled", "pass-through", "matched"] = "disabled"

        recalled: list[tuple[Candidate, float]] = []
        candidates_count = 0
        if automatic_recall_scope is None and self._retrieval is not None and query.strip():
            check_cancel(cancel_check)
            cands = self._retrieval.search(
                query, owner_user_id=owner, persona_id=persona_id, limit=effective_top_k
            )
            check_cancel(cancel_check)
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

        check_cancel(cancel_check)
        ctx = self._renderer.render(pinned=pinned, recalled=recalled, now=now)
        if source == "natural":
            ctx = self._with_core_profile(ctx, owner=owner, persona_id=persona_id)

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
                automatic_recall_scope=_scope_trace(automatic_recall_scope),
                scope_order_status=("unavailable" if automatic_recall_scope is not None else None),
                core_profile_exempt_count=sum(item.layer == "core" for item in ctx.items),
            )
            try:
                self._on_retrieved(trace)
            except Exception:
                logger.exception("on_retrieved callback failed; dropping trace")

        check_cancel(cancel_check)
        return ctx

    def _retrieve_with_search(
        self,
        query: str,
        *,
        persona_id: str,
        session_id: str | None,
        owner: str,
        top_k: int,
        source: Literal["natural", "probe"],
        now: datetime,
        automatic_recall_scope: CurrentSessionRecallScope | None,
        cancel_check: CancelCheck | None = None,
    ) -> MemoryContext:
        assert self._search_service is not None
        mode: Literal["automatic", "probe"] = "probe" if source == "probe" else "automatic"
        result = self._search_service.search(
            SearchRequest(
                text=query,
                owner_user_id=owner,
                persona_id=persona_id,
                mode=mode,
                session_id=session_id,
                top_k=top_k,
                automatic_recall_scope=automatic_recall_scope,
            ),
            cancel_check=cancel_check,
        )
        check_cancel(cancel_check)
        ctx = self._renderer.render_search(result.evidence, now=now)
        if source == "natural":
            ctx = self._with_core_profile(ctx, owner=owner, persona_id=persona_id)
        if self._on_retrieved is not None:
            ordinary_count = len(result.evidence)
            time_range = None
            if result.query.time_range is not None:
                time_range = {
                    "since": _iso(result.query.time_range.since),
                    "until": _iso(result.query.time_range.until),
                }
            decisions = {item.evidence_id: item for item in result.decisions}
            scope_decisions = {item.evidence_id: item for item in result.scope_decisions}
            ranked_candidates = {item.evidence_id: item for item in result.evidence}
            core_trace: list[dict[str, object]] = [
                {
                    "evidence_id": f"core:{item.source_ref}",
                    "kind": "core",
                    "source": "core_profile",
                    "keyword_rank": None,
                    "dense_rank": None,
                    "fused_score": None,
                    "scope_status": "eligible",
                    "scope_reason": "core_profile_exempt",
                    "current_session_source_refs": [],
                    "decision": "injected",
                    "decision_reason": "core_profile",
                }
                for item in ctx.items
                if item.layer == "core"
            ]
            candidate_trace: list[dict[str, object]] = []
            for candidate in result.candidates:
                scope_decision = scope_decisions.get(candidate.evidence_id)
                final_decision = decisions.get(candidate.evidence_id)
                ranked_candidate = ranked_candidates.get(candidate.evidence_id)
                candidate_trace.append(
                    {
                        "evidence_id": candidate.evidence_id,
                        "kind": candidate.kind,
                        "source": candidate.source_name,
                        "keyword_rank": candidate.keyword_rank,
                        "dense_rank": candidate.dense_rank,
                        "fused_score": (
                            ranked_candidate.fused_score if ranked_candidate is not None else None
                        ),
                        "scope_status": (
                            scope_decision.status if scope_decision is not None else None
                        ),
                        "scope_reason": (
                            scope_decision.reason if scope_decision is not None else None
                        ),
                        "current_session_source_refs": (
                            list(scope_decision.current_session_source_refs)
                            if scope_decision is not None
                            else []
                        ),
                        "decision": (
                            final_decision.decision if final_decision is not None else None
                        ),
                        "decision_reason": (
                            final_decision.reason if final_decision is not None else None
                        ),
                    }
                )
            candidate_trace.extend(core_trace)
            trace = RecallTrace(
                timestamp=now,
                query=query,
                owner_user_id=owner,
                persona_id=persona_id,
                top_k=top_k,
                source=source,
                pinned_pre_gate=0,
                pinned_post_gate=0,
                gate_enabled=False,
                gate_mode=None,
                gate_decision="disabled",
                candidates_count=sum(run.candidate_count for run in result.source_runs),
                ranked_count=ordinary_count,
                items=[
                    RecallTraceItem(
                        text=item.text,
                        layer=item.layer,
                        source_ref=item.source_ref,
                        score=item.score,
                    )
                    for item in ctx.items
                ],
                intent=result.query.intent,
                time_range=time_range,
                mode=mode,
                source_runs=[
                    {
                        "source": run.source_name,
                        "candidate_count": run.candidate_count,
                        "raw_candidate_count": run.raw_candidate_count,
                        "suppressed_count": run.suppressed_count,
                        "limit": run.limit,
                        "status": run.status,
                        "scope_scan_attempts": run.scope_scan_attempts,
                        "scope_scan_truncated": run.scope_scan_truncated,
                    }
                    for run in result.source_runs
                ],
                candidates=candidate_trace,
                budget={
                    "semantic": top_k,
                    "episodic": top_k,
                    "utterance": 4 if mode == "automatic" else top_k,
                    "total": top_k + (4 if mode == "automatic" else 0),
                },
                index_state=self._dense_index_state(),
                automatic_recall_scope=_scope_trace(automatic_recall_scope),
                scope_order_status=result.scope_order_status,
                core_profile_exempt_count=len(core_trace),
            )
            try:
                self._on_retrieved(trace)
            except Exception:
                check_cancel(cancel_check)
                logger.exception("on_retrieved callback failed; dropping trace")
        check_cancel(cancel_check)
        return ctx

    def _with_core_profile(
        self, context: MemoryContext, *, owner: str, persona_id: str
    ) -> MemoryContext:
        try:
            profile = self._store.core_profile(owner_user_id=owner, persona_id=persona_id)
        except CoreProfileError:
            logger.warning(
                "core profile unavailable owner=%s persona=%s",
                owner,
                persona_id,
                exc_info=True,
            )
            unavailable = "# 核心资料状态\n- Core Profile 暂不可用"
            rendered = unavailable
            if context.rendered:
                rendered += "\n\n" + context.rendered
            return MemoryContext(rendered=rendered, items=context.items)
        rendered_core = CoreProfileRenderer().render(profile)
        if not rendered_core:
            return context
        core_items = [
            MemoryItem(
                text=f"{entry.field_key} = {entry.value}",
                layer="core",
                source_ref=entry.id,
                score=999.0,
            )
            for entry in profile.entries
        ]
        rendered = rendered_core
        if context.rendered:
            rendered += "\n\n" + context.rendered
        return MemoryContext(rendered=rendered, items=[*core_items, *context.items])

    def _dense_index_state(self) -> dict[str, object]:
        if self._index_manager is None:
            return {
                "keyword_ready": True,
                "dense_status": "disabled",
                "active_generation": None,
            }
        state = self._index_manager.state()
        return {
            "keyword_ready": True,
            "dense_status": state.status,
            "active_generation": state.active_generation,
            "indexed_count": state.indexed_count,
            "total_count": state.total_count,
            "last_error": state.last_error,
        }

    # ----- 生命周期 -----

    def flush(self) -> None:
        """阻塞直到抽取队列清空。"""
        self._worker.flush()

    def close(self) -> None:
        """drain 抽取队列、停 worker、关库。幂等。"""
        self._worker.close()
        if self._index_manager is not None:
            self._index_manager.close()
        self._store.close()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _scope_trace(
    scope: CurrentSessionRecallScope | None,
) -> dict[str, str | None] | None:
    if scope is None:
        return None
    return {
        "current_session_id": scope.current_session_id,
        "recall_through_event_uuid": scope.recall_through_event_uuid,
    }
