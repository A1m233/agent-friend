"""SessionStore 原始对话事件的统一搜索 source 适配。"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

from memory.search import (
    AnalyzedQuery,
    EvidenceCandidate,
    EvidenceExcerpt,
    SearchRequest,
)

from ..sessions.events import Event
from ..sessions.store import SessionStore


class SessionHistorySource:
    """复用 SessionSummary 粗筛，再按需扫描 raw user/assistant events。"""

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
        summaries = [
            summary
            for summary in self._store.list()
            if not (
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

        candidates: list[EvidenceCandidate] = []
        for summary in summaries:
            session = self._store.load(summary.session_id)
            cutoff_idx = (
                request.current_turn_start_index
                if request.session_id == summary.session_id
                else None
            )
            previous: Event | None = None
            for index, event in enumerate(session.events):
                if cutoff_idx is not None and index >= cutoff_idx:
                    break
                if event.type not in ("user_message", "assistant_message"):
                    continue
                content = _recall_content(event)
                if content is None:
                    continue
                if (
                    time_range is not None
                    and time_range.since is not None
                    and event.ts < time_range.since
                ):
                    previous = event
                    continue
                if (
                    time_range is not None
                    and time_range.until is not None
                    and event.ts >= time_range.until
                ):
                    break
                if not _belongs_to_persona(event, request.persona_id):
                    previous = event
                    continue

                speaker = "user" if event.type == "user_message" else "agent"
                if request.said_by is not None and request.said_by != speaker:
                    previous = event
                    continue
                if query_terms and not _matches_query_terms(content, query_terms):
                    previous = event
                    continue

                excerpts: list[EvidenceExcerpt] = []
                if (
                    previous is not None
                    and previous.type in ("user_message", "assistant_message")
                    and _belongs_to_persona(previous, request.persona_id)
                ):
                    previous_content = _recall_content(previous)
                    if previous_content is not None:
                        excerpts.append(_excerpt(previous, previous_content))
                excerpts.append(_excerpt(event, content))
                if request.mode == "automatic" and event.type == "user_message":
                    following = _following_assistant(
                        session.events,
                        index=index,
                        cutoff_idx=cutoff_idx,
                        persona_id=request.persona_id,
                        since=time_range.since if time_range is not None else None,
                        until=time_range.until if time_range is not None else None,
                    )
                    if following is not None:
                        following_content = _recall_content(following)
                        if following_content is not None:
                            excerpts.append(_excerpt(following, following_content))
                candidates.append(
                    EvidenceCandidate(
                        evidence_id=f"session:{summary.session_id}#{event.uuid}",
                        source_name=self.name,
                        kind="utterance",
                        text=content,
                        owner_user_id=request.owner_user_id,
                        persona_id=_event_persona_id(event),
                        occurred_at=event.ts,
                        valid_from=None,
                        valid_until=None,
                        importance=0.5,
                        provenance=(f"{summary.session_id}#{event.uuid}",),
                        excerpts=tuple(excerpts),
                    )
                )
                previous = event

        candidates.sort(key=_candidate_occurred_at, reverse=True)
        return [
            replace(candidate, keyword_rank=rank if query_terms else None)
            for rank, candidate in enumerate(candidates[:limit], start=1)
        ]


def _recall_content(event: Event) -> str | None:
    if event.type == "assistant_message" and event.payload.get("partial"):
        return None
    content = event.payload.get("content", "")
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    return stripped if stripped else None


def _matches_query_terms(content: str, query_terms: tuple[str, ...]) -> bool:
    haystack = content.lower()
    return any(term in haystack for term in query_terms)


def _source_query_terms(query: AnalyzedQuery, request: SearchRequest) -> tuple[str, ...]:
    """显性工具保留 020 的空白分隔 OR 语义；automatic 使用共享中文分词。"""
    if request.mode != "explicit":
        return query.tokens
    return tuple(
        part.strip().lower()
        for part in re.split(r"[\s,，;；、|/]+", query.raw_text)
        if part.strip()
    )


def _event_persona_id(event: Event) -> str | None:
    persona_id = event.meta.get("persona_id")
    return persona_id if isinstance(persona_id, str) and persona_id else None


def _belongs_to_persona(event: Event, persona_id: str) -> bool:
    event_persona_id = _event_persona_id(event)
    return event_persona_id is None or not persona_id or event_persona_id == persona_id


def _excerpt(event: Event, content: str) -> EvidenceExcerpt:
    return EvidenceExcerpt(
        speaker="user" if event.type == "user_message" else "agent",
        text=content,
        occurred_at=event.ts,
    )


def _candidate_occurred_at(candidate: EvidenceCandidate) -> datetime:
    assert candidate.occurred_at is not None
    return candidate.occurred_at


def _following_assistant(
    events: list[Event],
    *,
    index: int,
    cutoff_idx: int | None,
    persona_id: str,
    since: datetime | None,
    until: datetime | None,
) -> Event | None:
    next_index = index + 1
    if next_index >= len(events) or (cutoff_idx is not None and next_index >= cutoff_idx):
        return None
    event = events[next_index]
    if event.type != "assistant_message" or _recall_content(event) is None:
        return None
    if not _belongs_to_persona(event, persona_id):
        return None
    if since is not None and event.ts < since:
        return None
    if until is not None and event.ts >= until:
        return None
    return event
