"""Provider-neutral types and errors for read-only social platform tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar, Literal, Protocol, runtime_checkable

from ...errors import ToolError

SocialPlatform = Literal["xiaohongshu", "bilibili"]
SocialContentType = Literal["note", "video", "article", "creator"]
SocialSort = Literal["relevance", "latest", "popular"]
SocialCreatorWorksSort = Literal["popular", "latest"]
SocialCreatorWorkRelationship = Literal["direct_upload", "collaboration"]
SocialCreatorWorksScope = Literal["all_public_works", "curated_subset"]
SocialReadView = Literal["detail", "works"]
EvidenceKind = Literal[
    "search_summary",
    "note_body",
    "video_description",
    "video_subtitle",
    "article_body",
    "creator_profile",
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
    identity: dict[str, str | int] = field(default_factory=dict)


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


@dataclass(frozen=True)
class SocialCreatorWork:
    locator: SocialResourceLocator
    title: str
    canonical_url: str
    published_at: str | None = None
    metrics: dict[str, int] = field(default_factory=dict)
    duration_seconds: int | None = None
    uploader: str = ""
    relationship: SocialCreatorWorkRelationship = "direct_upload"


@dataclass(frozen=True)
class SocialCreatorWorksResponse:
    creator_name: str
    creator_url: str
    works: tuple[SocialCreatorWork, ...]
    sort: SocialCreatorWorksSort
    page: int
    has_more: bool
    listing_scope: SocialCreatorWorksScope = "all_public_works"


class SocialPlatformError(ToolError):
    status: ClassVar[str] = "provider_error"
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool | None = None,
        diagnostic_stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.is_retryable = self.retryable if retryable is None else retryable
        self.diagnostic_stage = diagnostic_stage


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


@runtime_checkable
class SocialCreatorWorksConnector(Protocol):
    def creator_works(
        self,
        locator: SocialResourceLocator,
        *,
        sort: SocialCreatorWorksSort,
        page: int,
        page_size: int,
    ) -> SocialCreatorWorksResponse: ...
