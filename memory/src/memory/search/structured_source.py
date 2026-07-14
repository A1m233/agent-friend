"""结构化 memory.db 到统一证据候选的适配。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from ..store import EpisodicRow, SemanticRow, SqliteMemoryStore
from .contracts import AnalyzedQuery, EvidenceCandidate, SearchRequest, TimeRange

if TYPE_CHECKING:
    from .vector import EmbeddingIndexManager


class StructuredMemorySource:
    """组合 core、FTS5 keyword 与可选 dense 派生索引。"""

    name = "structured_memory"

    def __init__(
        self,
        store: SqliteMemoryStore,
        *,
        index_manager: EmbeddingIndexManager | None = None,
    ) -> None:
        self._store = store
        self._index_manager = index_manager

    def search(
        self,
        query: AnalyzedQuery,
        *,
        request: SearchRequest,
        limit: int,
    ) -> list[EvidenceCandidate]:
        core = [
            _semantic_candidate(row, None)
            for row in self._store.core(
                owner_user_id=request.owner_user_id,
                limit=20,
            )
        ]
        if not query.retrieval_text:
            return core
        since = query.time_range.since if query.time_range is not None else None
        until = query.time_range.until if query.time_range is not None else None
        semantic = self._store.search_semantic(
            query.retrieval_text,
            owner_user_id=request.owner_user_id,
            persona_id=request.persona_id,
            limit=limit,
            include_historical=query.include_historical,
            since=since,
            until=until,
        )
        if query.include_historical and query.time_range is None:
            semantic = [(row, score) for row, score in semantic if row.valid_until is not None]
        episodic = self._store.search_episodic(
            query.retrieval_text,
            owner_user_id=request.owner_user_id,
            persona_id=request.persona_id,
            limit=limit,
        )
        semantic_candidates = [
            _semantic_candidate(row, rank)
            for rank, (row, _) in enumerate(semantic, start=1)
            if _semantic_in_range(row, query.time_range)
        ]
        episodic_candidates = [
            _episodic_candidate(row, rank)
            for rank, (row, _) in enumerate(episodic, start=1)
            if _occurred_in_range(row.occurred_at, query.time_range)
        ]
        keyword_candidates: list[EvidenceCandidate] = []
        for index in range(max(len(semantic_candidates), len(episodic_candidates))):
            if index < len(semantic_candidates):
                keyword_candidates.append(semantic_candidates[index])
            if index < len(episodic_candidates):
                keyword_candidates.append(episodic_candidates[index])
        dense_candidates = self._dense_candidates(query, request=request, limit=limit)
        merged: dict[str, EvidenceCandidate] = {
            candidate.evidence_id: candidate for candidate in [*core, *keyword_candidates]
        }
        for candidate in dense_candidates:
            existing = merged.get(candidate.evidence_id)
            if existing is None:
                merged[candidate.evidence_id] = candidate
            else:
                merged[candidate.evidence_id] = replace(existing, dense_rank=candidate.dense_rank)
        return list(merged.values())

    def _dense_candidates(
        self,
        query: AnalyzedQuery,
        *,
        request: SearchRequest,
        limit: int,
    ) -> list[EvidenceCandidate]:
        if self._index_manager is None:
            return []
        hits = self._index_manager.search(
            query.retrieval_text,
            owner_user_id=request.owner_user_id,
            limit=limit,
        )
        semantic_ids = [
            hit.evidence_id.removeprefix("semantic:")
            for hit in hits
            if hit.evidence_id.startswith("semantic:")
        ]
        episodic_ids = [
            hit.evidence_id.removeprefix("episodic:")
            for hit in hits
            if hit.evidence_id.startswith("episodic:")
        ]
        semantic = self._store.semantic_by_ids(semantic_ids)
        episodic = self._store.episodic_by_ids(episodic_ids)
        candidates: list[EvidenceCandidate] = []
        for rank, hit in enumerate(hits, start=1):
            if hit.evidence_id.startswith("semantic:"):
                semantic_row = semantic.get(hit.evidence_id.removeprefix("semantic:"))
                if semantic_row is not None and _semantic_visible(semantic_row, query, request):
                    candidates.append(
                        replace(_semantic_candidate(semantic_row, None), dense_rank=rank)
                    )
            elif hit.evidence_id.startswith("episodic:"):
                episodic_row = episodic.get(hit.evidence_id.removeprefix("episodic:"))
                if (
                    episodic_row is not None
                    and episodic_row.deleted_at is None
                    and episodic_row.owner_user_id == request.owner_user_id
                    and episodic_row.persona_id == request.persona_id
                    and _occurred_in_range(episodic_row.occurred_at, query.time_range)
                ):
                    candidates.append(
                        replace(_episodic_candidate(episodic_row, None), dense_rank=rank)
                    )
        return candidates


def _semantic_candidate(row: SemanticRow, rank: int | None) -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id=f"semantic:{row.id}",
        source_name=StructuredMemorySource.name,
        kind="core" if row.memory_class == "core" else "semantic",
        text=row.statement,
        owner_user_id=row.owner_user_id,
        persona_id=row.persona_id,
        occurred_at=None,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        importance=row.importance,
        provenance=tuple(row.provenance),
        keyword_rank=rank,
    )


def _episodic_candidate(row: EpisodicRow, rank: int | None) -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id=f"episodic:{row.id}",
        source_name=StructuredMemorySource.name,
        kind="episodic",
        text=row.summary,
        owner_user_id=row.owner_user_id,
        persona_id=row.persona_id,
        occurred_at=row.occurred_at,
        valid_from=None,
        valid_until=None,
        importance=row.importance,
        provenance=(row.source_ref,),
        keyword_rank=rank,
    )


def _occurred_in_range(occurred_at: datetime, time_range: TimeRange | None) -> bool:
    if time_range is None:
        return True
    if time_range.since is not None and occurred_at < time_range.since:
        return False
    return not (time_range.until is not None and occurred_at >= time_range.until)


def _semantic_in_range(row: SemanticRow, time_range: TimeRange | None) -> bool:
    if time_range is None:
        return True
    start = row.valid_from or row.created_at
    end = row.valid_until
    if time_range.until is not None and start >= time_range.until:
        return False
    return not (time_range.since is not None and end is not None and end <= time_range.since)


def _semantic_visible(
    row: SemanticRow,
    query: AnalyzedQuery,
    request: SearchRequest,
) -> bool:
    if row.deleted_at is not None or row.owner_user_id != request.owner_user_id:
        return False
    if query.include_historical and query.time_range is None:
        return row.valid_until is not None
    if not query.include_historical and row.valid_until is not None:
        return False
    return _semantic_in_range(row, query.time_range)
