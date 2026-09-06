"""统一记忆检索编排服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC

from ..host_episodes import is_notification_episode_source
from ..host_episodes.presentation import project_notification_summary
from .analyzer import QueryAnalyzer
from .contracts import (
    AnalyzedQuery,
    CandidateDecision,
    CurrentSessionRecallScope,
    EvidenceCandidate,
    EvidenceSource,
    RecallScopeDecision,
    RecallScopeOrderStatus,
    SearchRequest,
    SearchResult,
    SessionSourceOrderProvider,
    SourceFailure,
    SourceRun,
)

_FINAL_LIMITS = {"automatic": 8, "explicit": 20, "probe": 20}
_SOURCE_LIMITS = {"automatic": 24, "explicit": 64, "probe": 64}
_HISTORY_INTENTS = {"history", "temporal", "last_interaction"}
_RRF_K = 60
_SCOPE_SCAN_LIMIT = 256

logger = logging.getLogger(__name__)


def _present_evidence(candidate: EvidenceCandidate) -> EvidenceCandidate:
    """Localize host notification text after ranking, keeping source evidence intact."""
    if candidate.kind != "episodic" or candidate.occurred_at is None:
        return candidate
    for source_ref in candidate.provenance:
        text = project_notification_summary(
            candidate.text,
            episode_id=candidate.evidence_id,
            source_ref=source_ref,
            occurred_at=candidate.occurred_at,
        )
        if text != candidate.text:
            return replace(candidate, text=text)
    return candidate


@dataclass(frozen=True)
class _ScopeContext:
    scope: CurrentSessionRecallScope
    positions: dict[str, int] | None
    cutoff_position: int | None
    order_status: RecallScopeOrderStatus


class MemorySearchService:
    """分析一次 query，调度已注册 sources，并做稳定去重与合并。"""

    def __init__(
        self,
        sources: tuple[EvidenceSource, ...] | list[EvidenceSource] = (),
        *,
        analyzer: QueryAnalyzer | None = None,
        source_order_provider: SessionSourceOrderProvider | None = None,
    ) -> None:
        self._analyzer = analyzer or QueryAnalyzer()
        self._source_order_provider = source_order_provider
        self._sources: dict[str, EvidenceSource] = {}
        for source in sources:
            self.register_source(source)

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(self._sources)

    @property
    def has_source_order_provider(self) -> bool:
        return self._source_order_provider is not None

    def register_source(self, source: EvidenceSource) -> None:
        if source.name in self._sources:
            raise ValueError(f"重复注册 memory search source: {source.name!r}")
        self._sources[source.name] = source

    def search(self, request: SearchRequest) -> SearchResult:
        query = self._analyzer.analyze(request)
        final_limit = request.top_k or _FINAL_LIMITS[request.mode]
        source_limit = _SOURCE_LIMITS[request.mode]
        candidates: list[EvidenceCandidate] = []
        considered_candidates: list[EvidenceCandidate] = []
        scope_decisions: list[RecallScopeDecision] = []
        failures: list[SourceFailure] = []
        source_runs: list[SourceRun] = []
        scope_context = self._prepare_scope_context(request)

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
                if scope_context is None:
                    raw_found = source.search(
                        query,
                        request=request,
                        limit=effective_source_limit + 1,
                    )
                    source_truncated = len(raw_found) > effective_source_limit
                    found = raw_found[:effective_source_limit]
                    considered_candidates.extend(found)
                    candidates.extend(found)
                    raw_count = len(raw_found)
                    suppressed_count = 0
                    scan_attempts = 1
                    scope_scan_truncated = False
                else:
                    (
                        raw_found,
                        found,
                        found_scope_decisions,
                        source_truncated,
                        scan_attempts,
                        scope_scan_truncated,
                    ) = self._search_source_with_scope(
                        source,
                        query=query,
                        request=request,
                        effective_source_limit=effective_source_limit,
                        scope_context=scope_context,
                    )
                    considered_candidates.extend(raw_found)
                    scope_decisions.extend(found_scope_decisions)
                    candidates.extend(found)
                    raw_count = len(raw_found)
                    suppressed_count = sum(
                        decision.status == "suppressed" for decision in found_scope_decisions
                    )
                source_runs.append(
                    SourceRun(
                        source_name=source.name,
                        candidate_count=len(found),
                        limit=effective_source_limit,
                        truncated=source_truncated,
                        raw_candidate_count=raw_count,
                        suppressed_count=suppressed_count,
                        scope_scan_attempts=scan_attempts,
                        scope_scan_truncated=scope_scan_truncated,
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
            evidence=tuple(_present_evidence(candidate) for candidate in ranked),
            failures=tuple(failures),
            source_runs=tuple(source_runs),
            decisions=tuple(decisions),
            candidates=tuple(considered_candidates),
            scope_decisions=tuple(scope_decisions),
            scope_order_status=(
                scope_context.order_status if scope_context is not None else "not_applied"
            ),
            truncated=(
                any(source_run.truncated for source_run in source_runs)
                or any(source_run.scope_scan_truncated for source_run in source_runs)
                or any(
                    decision.decision == "quota"
                    and decision.reason in {"ordinary_budget", "raw_evidence_budget", "core_budget"}
                    for decision in decisions
                )
            ),
        )

    def _prepare_scope_context(self, request: SearchRequest) -> _ScopeContext | None:
        scope = request.automatic_recall_scope
        if scope is None:
            return None
        if scope.recall_through_event_uuid is None:
            return _ScopeContext(
                scope=scope,
                positions=None,
                cutoff_position=None,
                order_status="not_required",
            )
        if self._source_order_provider is None:
            return _ScopeContext(
                scope=scope,
                positions=None,
                cutoff_position=None,
                order_status="unavailable",
            )
        try:
            event_uuids = self._source_order_provider.active_conversation_event_uuids(
                scope.current_session_id
            )
        except Exception:
            logger.warning(
                "automatic recall source order unavailable session=%s",
                scope.current_session_id,
                exc_info=True,
            )
            event_uuids = None
        if event_uuids is None:
            return _ScopeContext(
                scope=scope,
                positions=None,
                cutoff_position=None,
                order_status="unavailable",
            )
        positions = {event_uuid: index for index, event_uuid in enumerate(event_uuids)}
        cutoff_position = positions.get(scope.recall_through_event_uuid)
        return _ScopeContext(
            scope=scope,
            positions=positions,
            cutoff_position=cutoff_position,
            order_status="ready" if cutoff_position is not None else "cutoff_unresolved",
        )

    def _search_source_with_scope(
        self,
        source: EvidenceSource,
        *,
        query: AnalyzedQuery,
        request: SearchRequest,
        effective_source_limit: int,
        scope_context: _ScopeContext,
    ) -> tuple[
        list[EvidenceCandidate],
        list[EvidenceCandidate],
        list[RecallScopeDecision],
        bool,
        int,
        bool,
    ]:
        requested_limit = min(effective_source_limit + 1, _SCOPE_SCAN_LIMIT)
        raw_by_id: dict[str, EvidenceCandidate] = {}
        attempts = 0
        scope_scan_truncated = False

        while True:
            attempts += 1
            fetched = source.search(
                query,
                request=request,
                limit=requested_limit,
            )
            previous_count = len(raw_by_id)
            for candidate in fetched:
                if candidate.evidence_id not in raw_by_id:
                    raw_by_id[candidate.evidence_id] = candidate
                if len(raw_by_id) >= _SCOPE_SCAN_LIMIT:
                    break

            raw_candidates = list(raw_by_id.values())
            decisions = [_evaluate_scope(candidate, scope_context) for candidate in raw_candidates]
            eligible_ids = {
                decision.evidence_id for decision in decisions if decision.status == "eligible"
            }
            eligible = [
                candidate for candidate in raw_candidates if candidate.evidence_id in eligible_ids
            ]
            if len(eligible) > effective_source_limit:
                break

            source_exhausted = len(fetched) < requested_limit
            no_growth = len(raw_by_id) == previous_count
            if source_exhausted or no_growth:
                break
            if requested_limit >= _SCOPE_SCAN_LIMIT or len(raw_by_id) >= _SCOPE_SCAN_LIMIT:
                scope_scan_truncated = True
                break
            requested_limit = min(requested_limit * 2, _SCOPE_SCAN_LIMIT)

        source_truncated = len(eligible) > effective_source_limit or scope_scan_truncated
        return (
            raw_candidates,
            eligible[:effective_source_limit],
            decisions,
            source_truncated,
            attempts,
            scope_scan_truncated,
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


def _evaluate_scope(
    candidate: EvidenceCandidate,
    context: _ScopeContext,
) -> RecallScopeDecision:
    if candidate.kind == "core":
        return RecallScopeDecision(
            evidence_id=candidate.evidence_id,
            status="eligible",
            reason="core_profile_exempt",
        )

    if (
        candidate.kind == "episodic"
        and candidate.session_id is None
        and len(candidate.provenance) == 1
        and is_notification_episode_source(candidate.evidence_id, candidate.provenance[0])
    ):
        return RecallScopeDecision(
            evidence_id=candidate.evidence_id,
            status="eligible",
            reason="host_notification_event",
        )

    scope = context.scope
    current_session_id = scope.current_session_id
    known_sessions: set[str] = set()
    if candidate.session_id:
        known_sessions.add(candidate.session_id)

    current_refs: list[str] = []
    current_event_uuids: list[str] = []
    unresolved_current = False
    for raw_ref in candidate.provenance:
        parsed = _parse_source_ref(raw_ref)
        if parsed is None:
            continue
        session_id, event_uuids, malformed = parsed
        known_sessions.add(session_id)
        if session_id != current_session_id:
            continue
        if malformed:
            unresolved_current = True
            continue
        for event_uuid in event_uuids:
            normalized = f"{session_id}#{event_uuid}"
            if normalized not in current_refs:
                current_refs.append(normalized)
                current_event_uuids.append(event_uuid)

    known_current = current_session_id in known_sessions
    if not known_current:
        return RecallScopeDecision(
            evidence_id=candidate.evidence_id,
            status="eligible",
            reason="other_session" if known_sessions else "legacy_unscoped",
        )

    if scope.recall_through_event_uuid is None:
        return RecallScopeDecision(
            evidence_id=candidate.evidence_id,
            status="suppressed",
            reason="current_session_empty_boundary",
            current_session_source_refs=tuple(current_refs),
        )

    if (
        context.positions is None
        or context.cutoff_position is None
        or unresolved_current
        or not current_event_uuids
    ):
        return RecallScopeDecision(
            evidence_id=candidate.evidence_id,
            status="suppressed",
            reason="current_session_source_unresolved",
            current_session_source_refs=tuple(current_refs),
        )

    positions: list[int] = []
    missing = False
    for event_uuid in current_event_uuids:
        position = context.positions.get(event_uuid)
        if position is None:
            missing = True
        else:
            positions.append(position)
    if positions and any(position > context.cutoff_position for position in positions):
        return RecallScopeDecision(
            evidence_id=candidate.evidence_id,
            status="suppressed",
            reason="current_session_after_boundary",
            current_session_source_refs=tuple(current_refs),
        )
    if missing:
        return RecallScopeDecision(
            evidence_id=candidate.evidence_id,
            status="suppressed",
            reason="current_session_source_unresolved",
            current_session_source_refs=tuple(current_refs),
        )
    return RecallScopeDecision(
        evidence_id=candidate.evidence_id,
        status="eligible",
        reason="current_session_at_or_before_boundary",
        current_session_source_refs=tuple(current_refs),
    )


def _parse_source_ref(raw_ref: str) -> tuple[str, tuple[str, ...], bool] | None:
    session_id, separator, raw_events = raw_ref.partition("#")
    if not separator or not session_id:
        return None
    if not raw_events:
        return session_id, (), True
    if ".." not in raw_events:
        return session_id, (raw_events,), False
    parts = raw_events.split("..")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return session_id, (), True
    return session_id, (parts[0], parts[1]), False


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
