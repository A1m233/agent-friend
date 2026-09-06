"""Deterministic full-text search over user-visible session history."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol

from .catalog import SessionCatalog, SessionDescriptor
from .errors import SessionNotFoundError
from .events import Event
from .query import SourceVersion, query_session
from .store import SessionStore

SearchRole = Literal["user", "assistant"]
SearchMatchedField = Literal["content", "attachment_name"]

_EXCERPT_CONTEXT_CHARS = 64


class SessionRevisionSnapshot(Protocol):
    def current_revisions(self, session_ids: Iterable[str]) -> dict[str, int]: ...


@dataclass(frozen=True)
class SearchTextSegment:
    text: str
    matched: bool


@dataclass(frozen=True)
class SessionTitleSearchResult:
    result_id: str
    kind: Literal["title"]
    session_id: str
    session_title: str
    archived: bool
    updated_at: datetime
    segments: tuple[SearchTextSegment, ...]


@dataclass(frozen=True)
class SessionMessageSearchResult:
    result_id: str
    kind: Literal["message"]
    session_id: str
    session_title: str
    archived: bool
    role: SearchRole
    occurred_at: datetime
    anchor_event_uuid: str
    matched_field: SearchMatchedField
    segments: tuple[SearchTextSegment, ...]


@dataclass(frozen=True)
class SessionSearchResponse:
    query: str
    title_total: int
    message_total: int
    title_results: tuple[SessionTitleSearchResult, ...]
    message_results: tuple[SessionMessageSearchResult, ...]


@dataclass(frozen=True)
class _SearchTextBlock:
    event_uuid: str
    text: str


@dataclass(frozen=True)
class _SearchMessage:
    message_key: str
    role: SearchRole
    occurred_at: datetime
    content_blocks: tuple[_SearchTextBlock, ...]
    attachment_names: tuple[str, ...] = ()


@dataclass
class _MessageBuilder:
    message_key: str
    role: SearchRole
    occurred_at: datetime
    content_blocks: list[_SearchTextBlock] = field(default_factory=list)
    attachment_names: tuple[str, ...] = ()

    def freeze(self) -> _SearchMessage:
        return _SearchMessage(
            message_key=self.message_key,
            role=self.role,
            occurred_at=self.occurred_at,
            content_blocks=tuple(self.content_blocks),
            attachment_names=self.attachment_names,
        )


@dataclass(frozen=True)
class _CachedSearchDocument:
    revision: int
    updated_at: datetime
    messages: tuple[_SearchMessage, ...]
    source_version: SourceVersion | None = None


class SessionSearchService:
    """Build and reuse a revision-aware, process-local session search index."""

    def __init__(
        self,
        store: SessionStore,
        catalog: SessionCatalog,
        revisions: SessionRevisionSnapshot | None = None,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._revisions = revisions
        self._cache: dict[str, _CachedSearchDocument] = {}
        self._lock = RLock()

    def search(
        self,
        query: str,
        *,
        message_limit: int = 50,
        title_limit: int = 10,
    ) -> SessionSearchResponse:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("q 不能为空")
        if len(normalized_query) > 200:
            raise ValueError("q 不能超过 200 个字符")
        if not 1 <= message_limit <= 50:
            raise ValueError("message_limit 必须在 1 到 50 之间")
        if not 1 <= title_limit <= 10:
            raise ValueError("title_limit 必须在 1 到 10 之间")

        pattern = re.compile(re.escape(normalized_query), re.IGNORECASE)
        descriptors, documents = self._refresh_documents()
        title_results = self._match_titles(descriptors, pattern)
        message_results = self._match_messages(descriptors, documents, pattern)

        return SessionSearchResponse(
            query=normalized_query,
            title_total=len(title_results),
            message_total=len(message_results),
            title_results=tuple(title_results[:title_limit]),
            message_results=tuple(message_results[:message_limit]),
        )

    def _refresh_documents(
        self,
    ) -> tuple[tuple[SessionDescriptor, ...], dict[str, _CachedSearchDocument]]:
        descriptors = self._catalog.list_all()
        session_ids = tuple(item.session_id for item in descriptors)
        revisions = (
            self._revisions.current_revisions(session_ids)
            if self._revisions is not None
            else {session_id: 0 for session_id in session_ids}
        )
        live_ids = set(session_ids)
        with self._lock:
            for cached_id in tuple(self._cache):
                if cached_id not in live_ids:
                    self._cache.pop(cached_id, None)

        available_descriptors: list[SessionDescriptor] = []
        documents: dict[str, _CachedSearchDocument] = {}
        for descriptor in descriptors:
            revision = revisions.get(descriptor.session_id, 0)
            with self._lock:
                cached = self._cache.get(descriptor.session_id)
            previous_cached = cached
            snapshot = None
            # Native stores validate physical source identity even when a writer
            # bypassed the best-effort revision notification wrapper.
            if callable(getattr(self._store, "query", None)):
                try:
                    snapshot = query_session(self._store, descriptor.session_id)
                except SessionNotFoundError:
                    continue
            if (
                cached is None
                or (snapshot is not None and cached.source_version != snapshot.source_version)
                or (
                    snapshot is None
                    and (cached.revision != revision or cached.updated_at != descriptor.updated_at)
                )
            ):
                try:
                    snapshot = snapshot or query_session(self._store, descriptor.session_id)
                except SessionNotFoundError:
                    with self._lock:
                        self._cache.pop(descriptor.session_id, None)
                    continue
                cached = _CachedSearchDocument(
                    revision=revision,
                    updated_at=descriptor.updated_at,
                    messages=_project_search_messages(list(snapshot.active_events)),
                    source_version=snapshot.source_version,
                )
                with self._lock:
                    if self._cache.get(descriptor.session_id) is previous_cached:
                        self._cache[descriptor.session_id] = cached
                    # Text projections are derived and cheaply evicted; the store
                    # remains the sole owner of source freshness and file reads.
                    while len(self._cache) > 64:
                        self._cache.pop(next(iter(self._cache)))
            available_descriptors.append(descriptor)
            documents[descriptor.session_id] = cached

        return tuple(available_descriptors), documents

    @staticmethod
    def _match_titles(
        descriptors: tuple[SessionDescriptor, ...], pattern: re.Pattern[str]
    ) -> list[SessionTitleSearchResult]:
        results = [
            SessionTitleSearchResult(
                result_id=f"title:{descriptor.session_id}",
                kind="title",
                session_id=descriptor.session_id,
                session_title=descriptor.display_title,
                archived=descriptor.archived,
                updated_at=descriptor.updated_at,
                segments=_segments(descriptor.display_title, pattern),
            )
            for descriptor in descriptors
            if pattern.search(descriptor.display_title)
        ]
        results.sort(key=lambda item: (-item.updated_at.timestamp(), item.session_id))
        return results

    @staticmethod
    def _match_messages(
        descriptors: tuple[SessionDescriptor, ...],
        documents: dict[str, _CachedSearchDocument],
        pattern: re.Pattern[str],
    ) -> list[SessionMessageSearchResult]:
        results: list[SessionMessageSearchResult] = []
        for descriptor in descriptors:
            document = documents[descriptor.session_id]
            for message in document.messages:
                match = _first_message_match(message, pattern)
                if match is None:
                    continue
                field_name, anchor, text = match
                results.append(
                    SessionMessageSearchResult(
                        result_id=(f"message:{descriptor.session_id}:{message.message_key}"),
                        kind="message",
                        session_id=descriptor.session_id,
                        session_title=descriptor.display_title,
                        archived=descriptor.archived,
                        role=message.role,
                        occurred_at=message.occurred_at,
                        anchor_event_uuid=anchor,
                        matched_field=field_name,
                        segments=_excerpt_segments(text, pattern),
                    )
                )
        results.sort(
            key=lambda item: (
                -item.occurred_at.timestamp(),
                item.session_id,
                item.anchor_event_uuid,
            )
        )
        return results


def _project_search_messages(events: list[Event]) -> tuple[_SearchMessage, ...]:
    messages: list[_SearchMessage] = []
    hidden_run_ids: set[str] = set()
    current_assistant: _MessageBuilder | None = None
    pending_skip_assistant = False
    pending_reminder_card = False

    def finish_assistant() -> None:
        nonlocal current_assistant
        if current_assistant is not None and current_assistant.content_blocks:
            messages.append(current_assistant.freeze())
        current_assistant = None

    def ensure_assistant(event: Event) -> _MessageBuilder:
        nonlocal current_assistant
        if current_assistant is None:
            current_assistant = _MessageBuilder(
                message_key=event.uuid,
                role="assistant",
                occurred_at=event.ts,
            )
        return current_assistant

    for event in events:
        payload = event.payload
        run_id = _string(event.meta.get("run_id"))
        if event.type != "system_trigger" and run_id and run_id in hidden_run_ids:
            continue

        if event.type == "system_trigger":
            visibility = _string(payload.get("output_visibility"))
            presentation = _string(payload.get("presentation"))
            source_kind = _string(payload.get("source_kind"))
            has_reminder_card = source_kind == "todo_reminder" and pending_reminder_card
            pending_reminder_card = False
            if presentation == "none" and run_id:
                hidden_run_ids.add(run_id)
                finish_assistant()
                continue
            if visibility == "user" and (
                presentation != "conversation"
                or (source_kind == "todo_reminder" and not has_reminder_card)
            ):
                pending_skip_assistant = True
                if run_id:
                    hidden_run_ids.add(run_id)
                    pending_skip_assistant = False
                    finish_assistant()
            continue

        if event.type == "todo_reminder":
            if _string(payload.get("presentation")) == "none":
                pending_reminder_card = False
                continue
            finish_assistant()
            ensure_assistant(event)
            pending_reminder_card = True
            continue

        if event.type == "user_message":
            finish_assistant()
            messages.append(
                _SearchMessage(
                    message_key=event.uuid,
                    role="user",
                    occurred_at=event.ts,
                    content_blocks=(
                        _SearchTextBlock(
                            event_uuid=event.uuid, text=_string(payload.get("content"))
                        ),
                    ),
                    attachment_names=_attachment_names(payload.get("attachments")),
                )
            )
            pending_skip_assistant = False
            pending_reminder_card = False
            continue

        if event.type == "assistant_message":
            if pending_skip_assistant:
                pending_skip_assistant = False
                finish_assistant()
                continue
            content = _string(payload.get("content"))
            if content:
                ensure_assistant(event).content_blocks.append(
                    _SearchTextBlock(event_uuid=event.uuid, text=content)
                )
            continue

        if event.type == "run_error":
            if pending_skip_assistant:
                pending_skip_assistant = False
                finish_assistant()
                continue
            ensure_assistant(event)
            finish_assistant()
            continue

        if event.type == "tool_call_request":
            ensure_assistant(event)

    finish_assistant()
    return tuple(messages)


def _first_message_match(
    message: _SearchMessage, pattern: re.Pattern[str]
) -> tuple[SearchMatchedField, str, str] | None:
    for block in message.content_blocks:
        if pattern.search(block.text):
            return "content", block.event_uuid, block.text
    for attachment_name in message.attachment_names:
        if pattern.search(attachment_name):
            return "attachment_name", message.message_key, attachment_name
    return None


def _segments(text: str, pattern: re.Pattern[str]) -> tuple[SearchTextSegment, ...]:
    matches = tuple(pattern.finditer(text))
    if not matches:
        return (SearchTextSegment(text=text, matched=False),)
    segments: list[SearchTextSegment] = []
    cursor = 0
    for match in matches:
        if match.start() > cursor:
            segments.append(SearchTextSegment(text=text[cursor : match.start()], matched=False))
        segments.append(SearchTextSegment(text=match.group(0), matched=True))
        cursor = match.end()
    if cursor < len(text):
        segments.append(SearchTextSegment(text=text[cursor:], matched=False))
    return tuple(segments)


def _excerpt_segments(text: str, pattern: re.Pattern[str]) -> tuple[SearchTextSegment, ...]:
    first = pattern.search(text)
    if first is None:
        return (SearchTextSegment(text=text, matched=False),)
    start = max(0, first.start() - _EXCERPT_CONTEXT_CHARS)
    end = min(len(text), first.end() + _EXCERPT_CONTEXT_CHARS)
    excerpt = text[start:end]
    segments = list(_segments(excerpt, pattern))
    if start > 0:
        segments.insert(0, SearchTextSegment(text="…", matched=False))
    if end < len(text):
        segments.append(SearchTextSegment(text="…", matched=False))
    return tuple(segments)


def _attachment_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        asset_id = _string(item.get("asset_id"))
        display_name = _string(item.get("display_name"))
        media_type = _string(item.get("media_type"))
        kind = item.get("kind")
        if asset_id and display_name and media_type and (kind == "image" or kind == "document"):
            names.append(display_name)
    return tuple(names)


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
