"""Provider-neutral webpage extraction contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol


class WebContentError(Exception):
    kind = "provider"


class WebContentAuthError(WebContentError):
    kind = "auth"


class WebContentRateLimitError(WebContentError):
    kind = "rate_limit"


class WebContentProviderAccessError(WebContentError):
    kind = "provider_access_denied"


class WebContentNetworkError(WebContentError):
    kind = "network"


class WebContentTimeoutError(WebContentError):
    kind = "timeout"


class WebContentRedirectError(WebContentError):
    kind = "redirect_limit"


class WebContentHttpError(WebContentError):
    """A response from the target site, distinct from provider authentication."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.http_status = status_code
        self.kind = {
            401: "authentication_required",
            403: "access_denied",
            404: "not_found",
            410: "gone",
            408: "timeout",
            429: "rate_limit",
        }.get(status_code, "server_error" if status_code >= 500 else "http_error")


class WebContentEmptyError(WebContentError):
    kind = "empty_content"


class WebContentUnsupportedError(WebContentError):
    kind = "unsupported_content"


class WebContentResourceLimitError(WebContentError):
    kind = "resource_limit"


@dataclass(frozen=True)
class ExtractedLink:
    url: str
    label: str | None = None


@dataclass(frozen=True)
class ExtractedContent:
    requested_url: str
    final_url: str
    text: str
    content_kind: str = "html"
    links: tuple[ExtractedLink, ...] = ()
    link_total: int = 0
    links_truncated: bool = False


class WebContentProvider(Protocol):
    name: ClassVar[str]

    def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent: ...
