"""Read-only catalog for resolving user-visible session titles."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .metadata import SessionMetadataStore
from .store import SessionStore

_LEADING_TITLE_RE = re.compile(r"^\s*(.+?)\s*[（(【\[]")


@dataclass(frozen=True)
class SessionDescriptor:
    session_id: str
    display_title: str
    archived: bool
    updated_at: datetime

    @property
    def aliases(self) -> tuple[str, ...]:
        full = normalize_session_title(self.display_title)
        match = _LEADING_TITLE_RE.match(self.display_title)
        leading = normalize_session_title(match.group(1)) if match else ""
        if leading and leading != full:
            return (full, leading)
        return (full,)


SessionResolutionStatus = Literal["matched", "ambiguous", "not_found"]


@dataclass(frozen=True)
class SessionResolution:
    status: SessionResolutionStatus
    matched: SessionDescriptor | None = None
    candidates: tuple[SessionDescriptor, ...] = ()


class SessionCatalog:
    """Combine raw summaries with management metadata without owning mutations."""

    def __init__(
        self,
        store: SessionStore,
        metadata_store: SessionMetadataStore | None = None,
    ) -> None:
        self._store = store
        self._metadata_store = metadata_store

    def list_all(self) -> tuple[SessionDescriptor, ...]:
        metadata = self._metadata_store.load_all() if self._metadata_store is not None else {}
        descriptors: list[SessionDescriptor] = []
        for summary in self._store.list():
            item = metadata.get(summary.session_id)
            title = item.display_title if item is not None and item.display_title else summary.title
            descriptors.append(
                SessionDescriptor(
                    session_id=summary.session_id,
                    display_title=title,
                    archived=bool(item is not None and item.archived_at is not None),
                    updated_at=summary.updated_at,
                )
            )
        descriptors.sort(key=lambda item: (-item.updated_at.timestamp(), item.session_id))
        return tuple(descriptors)

    def resolve(self, title_query: str) -> SessionResolution:
        needle = normalize_session_title(title_query)
        if not needle:
            return SessionResolution("not_found")
        descriptors = self.list_all()
        levels = (
            lambda aliases: aliases[0] == needle,
            lambda aliases: needle in aliases[1:],
            lambda aliases: any(alias.startswith(needle) for alias in aliases),
            lambda aliases: any(needle in alias for alias in aliases),
        )
        for matches_level in levels:
            matches = tuple(item for item in descriptors if matches_level(item.aliases))
            if len(matches) == 1:
                return SessionResolution("matched", matched=matches[0])
            if len(matches) > 1:
                return SessionResolution("ambiguous", candidates=matches[:5])
        return SessionResolution("not_found")

    def resolve_from_text(self, text: str) -> SessionResolution:
        """Conservatively match only a full title or controlled leading alias in text."""
        normalized_text = normalize_session_title(text)
        matches: list[SessionDescriptor] = []
        for item in self.list_all():
            if any(alias and alias in normalized_text for alias in item.aliases):
                matches.append(item)
        if len(matches) == 1:
            return SessionResolution("matched", matched=matches[0])
        if len(matches) > 1:
            matches.sort(
                key=lambda item: (
                    -max(
                        (len(alias) for alias in item.aliases if alias in normalized_text),
                        default=0,
                    ),
                    -item.updated_at.timestamp(),
                    item.session_id,
                )
            )
            longest = max(
                (
                    len(alias)
                    for item in matches
                    for alias in item.aliases
                    if alias in normalized_text
                ),
                default=0,
            )
            strongest = tuple(
                item
                for item in matches
                if max(
                    (len(alias) for alias in item.aliases if alias in normalized_text), default=0
                )
                == longest
            )
            if len(strongest) == 1:
                return SessionResolution("matched", matched=strongest[0])
            return SessionResolution("ambiguous", candidates=tuple(matches[:5]))
        return SessionResolution("not_found")


def normalize_session_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split()).strip()
