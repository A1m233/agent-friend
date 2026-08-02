"""In-process authorization requests created by the Agent and approved on desktop."""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from .models import SocialPlatform

SocialMissingCapability = Literal[
    "note_search",
    "note_body",
    "video_subtitle",
    "article_body",
]
SocialAuthRequestStatus = Literal[
    "pending",
    "login_in_progress",
    "authenticated",
    "continuing",
    "completed",
    "declined",
    "cancelled",
    "expired",
    "login_failed",
    "continuation_failed",
]

_ACTIVE_STATUSES: frozenset[SocialAuthRequestStatus] = frozenset(
    {"pending", "login_in_progress", "authenticated", "continuing"}
)
_TERMINAL_STATUSES: frozenset[SocialAuthRequestStatus] = frozenset(
    {
        "completed",
        "declined",
        "cancelled",
        "expired",
        "login_failed",
        "continuation_failed",
    }
)


@dataclass(frozen=True)
class SocialAuthRequest:
    request_id: str
    session_id: str
    platform: SocialPlatform
    missing_capabilities: tuple[SocialMissingCapability, ...]
    reason: str
    origin_surface: str
    origin_destination_id: str | None
    status: SocialAuthRequestStatus
    created_at: datetime
    expires_at: datetime
    continuation_run_id: str | None = None
    error_kind: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status in _ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


class SocialAuthRequestNotFoundError(LookupError):
    pass


class SocialAuthRequestConflictError(RuntimeError):
    pass


class SocialAuthRequestStore:
    """Thread-safe, process-local request state with bounded lifetime."""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(minutes=10),
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl.total_seconds() <= 0:
            raise ValueError("social auth request ttl must be positive")
        self._ttl = ttl
        self._now = now_factory or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._requests: dict[str, SocialAuthRequest] = {}

    def create(
        self,
        *,
        session_id: str,
        platform: SocialPlatform,
        missing_capabilities: tuple[SocialMissingCapability, ...],
        reason: str,
        origin_surface: str,
        origin_destination_id: str | None,
    ) -> SocialAuthRequest:
        now = self._utc_now()
        with self._lock:
            self._expire_pending_locked(now)
            for item in self._requests.values():
                if (
                    item.session_id == session_id
                    and item.platform == platform
                    and item.status in _ACTIVE_STATUSES
                ):
                    return item
            request = SocialAuthRequest(
                request_id=f"social_auth_{uuid4().hex}",
                session_id=session_id,
                platform=platform,
                missing_capabilities=missing_capabilities,
                reason=reason,
                origin_surface=origin_surface,
                origin_destination_id=origin_destination_id,
                status="pending",
                created_at=now,
                expires_at=now + self._ttl,
            )
            self._requests[request.request_id] = request
            return request

    def get(self, request_id: str) -> SocialAuthRequest:
        now = self._utc_now()
        with self._lock:
            self._expire_pending_locked(now)
            try:
                return self._requests[request_id]
            except KeyError as exc:
                raise SocialAuthRequestNotFoundError(request_id) from exc

    def list(
        self,
        *,
        session_id: str | None = None,
        active_only: bool = False,
    ) -> tuple[SocialAuthRequest, ...]:
        now = self._utc_now()
        with self._lock:
            self._expire_pending_locked(now)
            values = tuple(self._requests.values())
        return tuple(
            item
            for item in values
            if (session_id is None or item.session_id == session_id)
            and (not active_only or item.is_active)
        )

    def transition(
        self,
        request_id: str,
        *,
        expected: Collection[SocialAuthRequestStatus],
        status: SocialAuthRequestStatus,
        continuation_run_id: str | None = None,
        error_kind: str | None = None,
    ) -> SocialAuthRequest:
        now = self._utc_now()
        with self._lock:
            self._expire_pending_locked(now)
            try:
                current = self._requests[request_id]
            except KeyError as exc:
                raise SocialAuthRequestNotFoundError(request_id) from exc
            if current.status not in expected:
                raise SocialAuthRequestConflictError(
                    f"social auth request {request_id} is {current.status}, expected {sorted(expected)}"
                )
            updated = replace(
                current,
                status=status,
                continuation_run_id=(
                    continuation_run_id
                    if continuation_run_id is not None
                    else current.continuation_run_id
                ),
                error_kind=error_kind,
            )
            self._requests[request_id] = updated
            return updated

    def close(self) -> None:
        with self._lock:
            for request_id, item in tuple(self._requests.items()):
                if item.status in _ACTIVE_STATUSES:
                    self._requests[request_id] = replace(item, status="cancelled")

    def _expire_pending_locked(self, now: datetime) -> None:
        for request_id, item in tuple(self._requests.items()):
            if item.status == "pending" and item.expires_at <= now:
                self._requests[request_id] = replace(item, status="expired")

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


__all__ = [
    "SocialAuthRequest",
    "SocialAuthRequestConflictError",
    "SocialAuthRequestNotFoundError",
    "SocialAuthRequestStatus",
    "SocialAuthRequestStore",
    "SocialMissingCapability",
]
