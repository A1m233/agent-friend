"""Provider-neutral types and errors for read-only social platform tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar, Literal, Protocol, runtime_checkable

from ...errors import ToolError

SocialPlatform = Literal["xiaohongshu", "bilibili"]
SocialContentType = Literal["note", "video", "article"]
SocialSort = Literal["relevance", "latest", "popular"]
EvidenceKind = Literal[
    "search_summary",
    "note_body",
    "video_description",
    "video_subtitle",
    "article_body",
]


@runtime_checkable
class SocialResourceLocator(Protocol):
    """Connector-private locator stored behind an opaque session reference."""

    @property
    def platform(self) -> SocialPlatform: ...

    @property
    def content_type(self) -> SocialContentType: ...


@dataclass(frozen=True)
class SocialSearchRequest:
    platform: SocialPlatform
    query: str
    content_types: tuple[SocialContentType, ...]
    sort: SocialSort
    published_after: date | None = None
    published_before: date | None = None
    limit: int = 10


@dataclass(frozen=True)
class SocialSearchCandidate:
    locator: SocialResourceLocator
    title: str
    author: str
    canonical_url: str
    summary: str = ""
    published_at: str | None = None
    metrics: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SocialSearchResponse:
    candidates: tuple[SocialSearchCandidate, ...]
    applied_filters: tuple[str, ...] = ()
    unsupported_filters: tuple[str, ...] = ()


@dataclass(frozen=True)
class SocialResolvedUrl:
    """A connector-owned interpretation of a supported platform URL."""

    platform: SocialPlatform
    content_type: SocialContentType
    canonical_url: str
    locator: SocialResourceLocator | None


@dataclass(frozen=True)
class SocialEvidence:
    kind: EvidenceKind
    text: str


@dataclass(frozen=True)
class SocialReadResponse:
    title: str
    author: str
    canonical_url: str
    evidence: tuple[SocialEvidence, ...]
    unavailable_parts: tuple[str, ...] = ()
    unavailable_reasons: dict[str, str] = field(default_factory=dict)
    published_at: str | None = None
    metrics: dict[str, int] = field(default_factory=dict)


class SocialPlatformError(ToolError):
    status: ClassVar[str] = "provider_error"
    retryable: ClassVar[bool] = False

    def __init__(self, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.is_retryable = self.retryable if retryable is None else retryable


class SocialConnectorUnavailableError(SocialPlatformError):
    status = "connector_unavailable"


class SocialConnectorIncompatibleError(SocialPlatformError):
    status = "connector_incompatible"


class SocialAuthRequiredError(SocialPlatformError):
    status = "auth_required"


class SocialAuthExpiredError(SocialPlatformError):
    status = "auth_expired"


class SocialAccessRestrictedError(SocialPlatformError):
    status = "access_restricted"


class SocialContentNotFoundError(SocialPlatformError):
    status = "not_found"


class SocialResourceExpiredError(SocialPlatformError):
    status = "resource_expired"


class SocialResourceNotFoundError(SocialPlatformError):
    status = "resource_not_found"


class SocialUrlNotAuthorizedError(SocialPlatformError):
    status = "url_not_authorized"


class SocialUrlUnsupportedError(SocialPlatformError):
    status = "url_unsupported"


class SocialNetworkError(SocialPlatformError):
    status = "network_error"
    retryable = True


class SocialRateLimitedError(SocialPlatformError):
    status = "rate_limited"


class SocialProviderError(SocialPlatformError):
    status = "provider_error"


@runtime_checkable
class SocialPlatformConnector(Protocol):
    platform: ClassVar[SocialPlatform]
    name: ClassVar[str]
    version: ClassVar[str]

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse: ...

    def resolve_url(self, url: str) -> SocialResolvedUrl | None: ...

    def read(self, locator: SocialResourceLocator) -> SocialReadResponse: ...

    def close(self) -> None: ...
