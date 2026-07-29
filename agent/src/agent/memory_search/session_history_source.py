"""SessionStore conversation evidence adapter for unified memory search."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC

from memory.search import (
    AnalyzedQuery,
    EvidenceCandidate,
    EvidenceExcerpt,
    SearchRequest,
    TimeRange,
)

from ..sessions.events import Event
from ..sessions.store import SessionStore

_EXCERPT_CHARS = 240


class SessionHistorySource:
    """Search active user/assistant events with optional hard session scope."""

    name = "session_history"

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def search(
        self,
        query: AnalyzedQuery,
        *,
        request: SearchRequest,
        limit: int,
    ) -> list[EvidenceCandidate]:
        time_range = query.time_range
        query_terms = _source_query_terms(query, request)
        target_ids = set(request.target_session_ids)
        summaries = [
            summary
            for summary in self._store.list()
            if (not target_ids or summary.session_id in target_ids)
            and not (
                time_range is not None
                and time_range.until is not None
                and summary.created_at >= time_range.until
            )
            and not (
                time_range is not None
                and time_range.since is not None
                and summary.updated_at < time_range.since
            )
        ]

        ranked: list[tuple[tuple[int, int, float, str], EvidenceCandidate]] = []
        for summary in summaries:
            session = self._store.load(summary.session_id)
            excluded_uuids = _current_turn_uuids(session.events, summary.session_id, request)
            active_conversation = [
                event
                for event in session.active_events
                if event.uuid not in excluded_uuids
                and event.type in ("user_message", "assistant_message")
                and _recall_content(event) is not None
            ]
            for turn in _conversation_turns(active_conversation):
                visible_events = tuple(
                    event
                    for event in turn
                    if _in_time_range(event, time_range)
                    and _belongs_to_persona(event, request.persona_id)
                    and _matches_said_by(event, request.said_by)
                )
                if not visible_events:
                    continue

                scored_events = [
                    (
                        _match_quality(
                            _recall_content(event) or "",
                            query.retrieval_text,
                            query_terms,
                        ),
                        event,
                    )
                    for event in visible_events
                ]
                matching_events = [
                    (score, event)
                    for score, event in scored_events
                    if not query_terms or score[0] > 0
                ]
                if not matching_events:
                    continue
                (match_level, coverage), matched = max(
                    matching_events,
                    key=lambda item: (
                        item[0][0],
                        item[0][1],
                        _timestamp(item[1]),
                        item[1].uuid,
                    ),
                )
                content = _recall_content(matched)
                assert content is not None
                window = _turn_excerpts(
                    visible_events,
                    match_terms=query_terms,
                )
                turn_anchor = turn[0]
                candidate = EvidenceCandidate(
                    evidence_id=f"session:{summary.session_id}#turn:{turn_anchor.uuid}",
                    source_name=self.name,
                    kind="utterance",
                    text=content,
                    owner_user_id=request.owner_user_id,
                    persona_id=_event_persona_id(matched),
                    occurred_at=visible_events[-1].ts,
                    valid_from=None,
                    valid_until=None,
                    importance=0.5,
                    provenance=tuple(
                        f"{summary.session_id}#{event.uuid}" for event in visible_events
                    ),
                    excerpts=window,
                    session_id=summary.session_id,
                )
                ranked.append(
                    (
                        (
                            match_level,
                            coverage,
                            _timestamp(visible_events[-1]),
                            turn_anchor.uuid,
                        ),
                        candidate,
                    )
                )

        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = _select_with_session_coverage(ranked, limit=limit, scoped=bool(target_ids))
        return [
            replace(
                candidate,
                keyword_rank=(
                    rank
                    if query_terms
                    or (request.mode == "explicit" and request.retrieval_goal == "summary")
                    else None
                ),
            )
            for rank, candidate in enumerate(selected, start=1)
        ]


def _current_turn_uuids(
    raw_events: list[Event],
    session_id: str,
    request: SearchRequest,
) -> set[str]:
    if request.session_id != session_id or request.current_turn_start_index is None:
        return set()
    return {event.uuid for event in raw_events[request.current_turn_start_index :]}


def _recall_content(event: Event) -> str | None:
    if event.type == "assistant_message" and event.payload.get("partial"):
        return None
    content = event.payload.get("content", "")
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    return stripped if stripped else None


def _source_query_terms(
    query: AnalyzedQuery,
    request: SearchRequest,
) -> tuple[str, ...]:
    if request.mode != "explicit":
        return tuple(term.casefold() for term in query.tokens if term.strip())
    return tuple(
        part.strip().casefold()
        for part in re.split(r"[\s,，;；、|/]+", query.retrieval_text)
        if part.strip()
    )


def _match_quality(
    content: str,
    retrieval_text: str,
    query_terms: tuple[str, ...],
) -> tuple[int, int]:
    if not query_terms:
        return 1, 0
    haystack = content.casefold()
    phrase = retrieval_text.strip().casefold()
    hits = sum(term in haystack for term in query_terms)
    if phrase and phrase in haystack:
        return 4, hits
    if hits == len(query_terms):
        return 3, hits
    if hits:
        return 2, hits
    return 0, 0


def _in_time_range(event: Event, time_range: TimeRange | None) -> bool:
    if time_range is None:
        return True
    since = getattr(time_range, "since", None)
    until = getattr(time_range, "until", None)
    return not (
        (since is not None and event.ts < since) or (until is not None and event.ts >= until)
    )


def _event_persona_id(event: Event) -> str | None:
    persona_id = event.meta.get("persona_id")
    return persona_id if isinstance(persona_id, str) and persona_id else None


def _belongs_to_persona(event: Event, persona_id: str) -> bool:
    event_persona_id = _event_persona_id(event)
    return event_persona_id is None or not persona_id or event_persona_id == persona_id


def _conversation_turns(events: list[Event]) -> list[tuple[Event, ...]]:
    """Group active conversation events into stable user-led turns.

    A user message starts a new turn. Consecutive assistant messages stay with
    that turn so tool-loop continuations do not become duplicate evidence.
    Leading assistant-only messages form their own turn until the next user
    message arrives.
    """
    turns: list[tuple[Event, ...]] = []
    current: list[Event] = []
    for event in events:
        if event.type == "user_message" and current:
            turns.append(tuple(current))
            current = []
        current.append(event)
    if current:
        turns.append(tuple(current))
    return turns


def _turn_excerpts(
    events: tuple[Event, ...],
    *,
    match_terms: tuple[str, ...],
) -> tuple[EvidenceExcerpt, ...]:
    return tuple(
        EvidenceExcerpt(
            speaker="user" if event.type == "user_message" else "agent",
            text=_match_aware_excerpt(
                _recall_content(event) or "",
                match_terms
                if _contains_any_term(_recall_content(event) or "", match_terms)
                else (),
            ),
            occurred_at=event.ts,
        )
        for event in events
    )


def _contains_any_term(content: str, terms: tuple[str, ...]) -> bool:
    haystack = content.casefold()
    return any(term in haystack for term in terms)


def _matches_said_by(event: Event, said_by: str | None) -> bool:
    if said_by is None:
        return True
    speaker = "user" if event.type == "user_message" else "agent"
    return speaker == said_by


def _match_aware_excerpt(text: str, terms: tuple[str, ...]) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= _EXCERPT_CHARS:
        return cleaned
    folded = cleaned.casefold()
    positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - _EXCERPT_CHARS // 3)
    end = min(len(cleaned), start + _EXCERPT_CHARS)
    start = max(0, end - _EXCERPT_CHARS)
    prefix = "…" if start else ""
    suffix = "…" if end < len(cleaned) else ""
    return f"{prefix}{cleaned[start:end]}{suffix}"


def _timestamp(event: Event) -> float:
    timestamp = event.ts if event.ts.tzinfo is not None else event.ts.replace(tzinfo=UTC)
    return timestamp.timestamp()


def _select_with_session_coverage(
    ranked: list[tuple[tuple[int, int, float, str], EvidenceCandidate]],
    *,
    limit: int,
    scoped: bool,
) -> list[EvidenceCandidate]:
    if scoped or not ranked:
        return [candidate for _, candidate in ranked[:limit]]
    selected: list[EvidenceCandidate] = []
    selected_ids: set[str] = set()
    levels = sorted({score[0] for score, _ in ranked}, reverse=True)
    for level in levels:
        band = [(score, candidate) for score, candidate in ranked if score[0] == level]
        seen_sessions: set[str | None] = set()
        for _, candidate in band:
            if candidate.session_id in seen_sessions:
                continue
            selected.append(candidate)
            selected_ids.add(candidate.evidence_id)
            seen_sessions.add(candidate.session_id)
            if len(selected) >= limit:
                return selected
        for _, candidate in band:
            if candidate.evidence_id in selected_ids:
                continue
            selected.append(candidate)
            selected_ids.add(candidate.evidence_id)
            if len(selected) >= limit:
                return selected
    return selected
