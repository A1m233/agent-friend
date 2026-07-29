"""统一记忆检索编排服务。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC

from .analyzer import QueryAnalyzer
from .contracts import (
    AnalyzedQuery,
    CandidateDecision,
    EvidenceCandidate,
    EvidenceSource,
    SearchRequest,
    SearchResult,
    SourceFailure,
    SourceRun,
)

_FINAL_LIMITS = {"automatic": 8, "explicit": 20, "probe": 20}
_SOURCE_LIMITS = {"automatic": 24, "explicit": 64, "probe": 64}
_HISTORY_INTENTS = {"history", "temporal", "last_interaction"}
_RRF_K = 60


class MemorySearchService:
    """分析一次 query，调度已注册 sources，并做稳定去重与合并。"""

    def __init__(
        self,
        sources: tuple[EvidenceSource, ...] | list[EvidenceSource] = (),
        *,
        analyzer: QueryAnalyzer | None = None,
    ) -> None:
        self._analyzer = analyzer or QueryAnalyzer()
        self._sources: dict[str, EvidenceSource] = {}
        for source in sources:
            self.register_source(source)

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(self._sources)

    def register_source(self, source: EvidenceSource) -> None:
        if source.name in self._sources:
            raise ValueError(f"重复注册 memory search source: {source.name!r}")
        self._sources[source.name] = source

    def search(self, request: SearchRequest) -> SearchResult:
        query = self._analyzer.analyze(request)
        final_limit = request.top_k or _FINAL_LIMITS[request.mode]
        source_limit = _SOURCE_LIMITS[request.mode]
        candidates: list[EvidenceCandidate] = []
        failures: list[SourceFailure] = []
        source_runs: list[SourceRun] = []

        for source in self._ordered_sources():
            if request.source_scope is not None and source.name not in request.source_scope:
                continue
            if source.name == "session_history" and not self._should_search_history(
                request,
                query,
                candidates,
            ):
                source_runs.append(
                    SourceRun(
                        source_name=source.name,
                        candidate_count=0,
                        limit=0,
                        status="skipped",
                    )
                )
                continue
            effective_source_limit = (
                8
                if request.mode == "automatic" and source.name == "session_history"
                else source_limit
            )
            try:
                found = source.search(
                    query,
                    request=request,
                    limit=effective_source_limit + 1,
                )
                source_truncated = len(found) > effective_source_limit
                found = found[:effective_source_limit]
                candidates.extend(found)
                source_runs.append(
                    SourceRun(
                        source_name=source.name,
                        candidate_count=len(found),
                        limit=effective_source_limit,
                        truncated=source_truncated,
                    )
                )
            except Exception as exc:
                failures.append(SourceFailure(source_name=source.name, message=str(exc)))
                source_runs.append(
                    SourceRun(
                        source_name=source.name,
                        candidate_count=0,
                        limit=effective_source_limit,
                        status="failed",
                    )
                )

        ranked, decisions = _fuse(candidates, request=request, ordinary_limit=final_limit)
        return SearchResult(
            request=request,
            query=query,
            evidence=tuple(ranked),
            failures=tuple(failures),
            source_runs=tuple(source_runs),
            decisions=tuple(decisions),
            truncated=(
                any(source_run.truncated for source_run in source_runs)
                or any(
                    decision.decision == "quota"
                    and decision.reason in {"ordinary_budget", "raw_evidence_budget", "core_budget"}
                    for decision in decisions
                )
            ),
        )

    def _ordered_sources(self) -> list[EvidenceSource]:
        return sorted(
            self._sources.values(),
            key=lambda source: (source.name == "session_history", source.name),
        )

    @staticmethod
    def _should_search_history(
        request: SearchRequest,
        query: AnalyzedQuery,
        current_candidates: list[EvidenceCandidate],
    ) -> bool:
        if request.mode in ("explicit", "probe"):
            return True
        structured = [candidate for candidate in current_candidates if candidate.kind != "core"]
        if not structured:
            return bool(query.tokens) or query.intent in _HISTORY_INTENTS
        if _has_query_coverage(structured, query.tokens):
            return False
        return bool(query.tokens) or query.intent in _HISTORY_INTENTS


def _fuse(
    candidates: list[EvidenceCandidate],
    *,
    request: SearchRequest,
    ordinary_limit: int,
) -> tuple[list[EvidenceCandidate], list[CandidateDecision]]:
    """RRF 融合、跨源文本去重，以及 core / ordinary 独立预算。"""
    decisions: list[CandidateDecision] = []
    eligible: list[EvidenceCandidate] = []
    for candidate in candidates:
        if (
            request.mode == "explicit"
            and candidate.kind == "core"
            and candidate.keyword_rank is None
            and candidate.dense_rank is None
        ):
            decisions.append(
                CandidateDecision(candidate.evidence_id, "quota", "explicit_unmatched_core")
            )
            continue
        eligible.append(candidate)
    scored = [replace(candidate, fused_score=_rrf_score(candidate)) for candidate in eligible]
    scored.sort(key=_rank_key)
    if request.mode == "explicit":
        scored.sort(key=lambda candidate: candidate.kind != "utterance")

    unique: list[EvidenceCandidate] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    for candidate in scored:
        normalized = " ".join(candidate.text.lower().split())
        if candidate.evidence_id in seen_ids or normalized in seen_text:
            decisions.append(
                CandidateDecision(candidate.evidence_id, "duplicate", "evidence_id_or_text")
            )
            continue
        seen_ids.add(candidate.evidence_id)
        seen_text.add(normalized)
        unique.append(candidate)

    core: list[EvidenceCandidate] = []
    core_chars = 0
    ordinary: list[EvidenceCandidate] = []
    raw_evidence: list[EvidenceCandidate] = []
    for candidate in unique:
        if candidate.kind == "core":
            if len(core) < 6 and core_chars + len(candidate.text) <= 320:
                core.append(candidate)
                core_chars += len(candidate.text)
                decisions.append(
                    CandidateDecision(candidate.evidence_id, "injected", "core_budget")
                )
            else:
                decisions.append(CandidateDecision(candidate.evidence_id, "quota", "core_budget"))
        elif request.mode == "automatic" and candidate.kind == "utterance":
            if len(raw_evidence) < 4:
                raw_evidence.append(candidate)
                decisions.append(
                    CandidateDecision(candidate.evidence_id, "injected", "raw_evidence_budget")
                )
            else:
                decisions.append(
                    CandidateDecision(candidate.evidence_id, "quota", "raw_evidence_budget")
                )
        elif len(ordinary) < ordinary_limit:
            ordinary.append(candidate)
            decisions.append(
                CandidateDecision(candidate.evidence_id, "injected", "ordinary_budget")
            )
        else:
            decisions.append(CandidateDecision(candidate.evidence_id, "quota", "ordinary_budget"))
    return [*core, *ordinary, *raw_evidence], decisions


def _rrf_score(candidate: EvidenceCandidate) -> float:
    score = 0.0
    if candidate.keyword_rank is not None:
        score += 1.0 / (_RRF_K + candidate.keyword_rank)
    if candidate.dense_rank is not None:
        score += 1.0 / (_RRF_K + candidate.dense_rank)
    if score == 0.0:
        score = 1.0 / (_RRF_K + 1_000)
    return score + min(max(candidate.importance, 0.0), 1.0) * 0.002


def _has_query_coverage(candidates: list[EvidenceCandidate], query_tokens: tuple[str, ...]) -> bool:
    """候选至少覆盖一半有效 query token，才算足以跳过 raw history。"""
    if not query_tokens:
        return False
    required = max(1, (len(query_tokens) + 1) // 2)
    for candidate in candidates:
        text = candidate.text.lower()
        if sum(token in text for token in query_tokens) >= required:
            return True
    return False


def _rank_key(candidate: EvidenceCandidate) -> tuple[float, float, str, str]:
    occurred_at = candidate.occurred_at or candidate.valid_from
    if occurred_at is None:
        recency_key = float("inf")
    else:
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        recency_key = -occurred_at.timestamp()
    return (
        -(candidate.fused_score or 0.0),
        recency_key,
        candidate.source_name,
        candidate.evidence_id,
    )
