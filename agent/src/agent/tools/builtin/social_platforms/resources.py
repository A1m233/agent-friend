"""Session-scoped in-memory resource references for social platform locators."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .models import (
    SocialContentType,
    SocialPlatform,
    SocialResourceExpiredError,
    SocialResourceLocator,
    SocialResourceNotFoundError,
)


@dataclass(frozen=True)
class SocialResourceEntry:
    resource_ref: str
    session_id: str
    platform: SocialPlatform
    content_type: SocialContentType
    canonical_url: str
    locator: SocialResourceLocator
    connector_version: str
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class SocialContinuationEntry:
    continuation_ref: str
    session_id: str
    platform: SocialPlatform
    content_type: SocialContentType
    canonical_url: str
    locator: SocialResourceLocator
    connector_version: str
    sort: str
    page: int
    expires_at: float


class SocialResourceStore:
    """Bounded volatile store; connector tokens never enter session history."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 30 * 60,
        max_per_session: int = 100,
        max_total: int = 1000,
        clock: Callable[[], float] = time.monotonic,
        make_ref: Callable[[], str] | None = None,
    ) -> None:
        if ttl_seconds <= 0 or max_per_session <= 0 or max_total <= 0:
            raise ValueError("resource store limits must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_per_session = max_per_session
        self._max_total = max_total
        self._clock = clock
        self._make_ref = make_ref or (lambda: f"social_ref_{secrets.token_hex(16)}")
        self._entries: OrderedDict[tuple[str, str], SocialResourceEntry] = OrderedDict()
        self._expired: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._lock = threading.Lock()

    def put(
        self,
        *,
        session_id: str,
        platform: SocialPlatform,
        content_type: SocialContentType,
        canonical_url: str,
        locator: SocialResourceLocator,
        connector_version: str,
    ) -> SocialResourceEntry:
        if not session_id:
            raise ValueError("trusted session_id is required")
        now = self._clock()
        with self._lock:
            self._prune(now)
            resource_ref = self._unique_ref(session_id)
            entry = SocialResourceEntry(
                resource_ref=resource_ref,
                session_id=session_id,
                platform=platform,
                content_type=content_type,
                canonical_url=canonical_url,
                locator=locator,
                connector_version=connector_version,
                created_at=now,
                expires_at=now + self._ttl_seconds,
            )
            key = (session_id, resource_ref)
            self._entries[key] = entry
            self._evict_session_overflow(session_id)
            while len(self._entries) > self._max_total:
                self._entries.popitem(last=False)
            return entry

    def get(self, *, session_id: str, resource_ref: str) -> SocialResourceEntry:
        now = self._clock()
        key = (session_id, resource_ref)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at <= now:
                self._entries.pop(key, None)
                self._expired[key] = now + self._ttl_seconds
                raise SocialResourceExpiredError("social resource reference expired")
            if entry is not None:
                return entry
            tombstone_until = self._expired.get(key)
            if tombstone_until is not None and tombstone_until > now:
                raise SocialResourceExpiredError("social resource reference expired")
            self._prune(now)
        raise SocialResourceNotFoundError("social resource reference not found")

    def _unique_ref(self, session_id: str) -> str:
        for _ in range(8):
            candidate = self._make_ref()
            if (session_id, candidate) not in self._entries:
                return candidate
        raise RuntimeError("could not allocate unique social resource reference")

    def _evict_session_overflow(self, session_id: str) -> None:
        session_keys = [key for key in self._entries if key[0] == session_id]
        for key in session_keys[: max(0, len(session_keys) - self._max_per_session)]:
            self._entries.pop(key, None)

    def _prune(self, now: float) -> None:
        expired_keys = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._entries.pop(key, None)
            self._expired[key] = now + self._ttl_seconds
        stale_tombstones = [key for key, until in self._expired.items() if until <= now]
        for key in stale_tombstones:
            self._expired.pop(key, None)
        while len(self._expired) > self._max_total:
            self._expired.popitem(last=False)


class SocialContinuationStore:
    """Bounded, session-scoped cursors whose opaque refs may enter model context."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 30 * 60,
        max_total: int = 1000,
        clock: Callable[[], float] = time.monotonic,
        make_ref: Callable[[], str] | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_total = max_total
        self._clock = clock
        self._make_ref = make_ref or (lambda: f"continuation_ref_{secrets.token_hex(16)}")
        self._entries: OrderedDict[tuple[str, str], SocialContinuationEntry] = OrderedDict()
        self._lock = threading.Lock()

    def put(
        self,
        *,
        session_id: str,
        platform: SocialPlatform,
        content_type: SocialContentType,
        canonical_url: str,
        locator: SocialResourceLocator,
        connector_version: str,
        sort: str,
        page: int,
    ) -> SocialContinuationEntry:
        if not session_id or page < 1:
            raise ValueError("invalid continuation identity")
        now = self._clock()
        entry = SocialContinuationEntry(
            continuation_ref=self._make_ref(),
            session_id=session_id,
            platform=platform,
            content_type=content_type,
            canonical_url=canonical_url,
            locator=locator,
            connector_version=connector_version,
            sort=sort,
            page=page,
            expires_at=now + self._ttl_seconds,
        )
        with self._lock:
            self._prune(now)
            self._entries[(session_id, entry.continuation_ref)] = entry
            while len(self._entries) > self._max_total:
                self._entries.popitem(last=False)
        return entry

    def get(self, *, session_id: str, continuation_ref: str) -> SocialContinuationEntry:
        now = self._clock()
        key = (session_id, continuation_ref)
        with self._lock:
            self._prune(now)
            entry = self._entries.get(key)
        if entry is None:
            raise SocialResourceNotFoundError("social continuation reference not found")
        return entry

    def _prune(self, now: float) -> None:
        for key in [key for key, entry in self._entries.items() if entry.expires_at <= now]:
            self._entries.pop(key, None)
