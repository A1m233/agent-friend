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


class WebContentNetworkError(WebContentError):
    kind = "network"


class WebContentEmptyError(WebContentError):
    kind = "empty_content"


class WebContentUnsupportedError(WebContentError):
    kind = "unsupported_content"


class WebContentResourceLimitError(WebContentError):
    kind = "resource_limit"


@dataclass(frozen=True)
class ExtractedContent:
    requested_url: str
    final_url: str
    text: str
    content_kind: str = "html"


class WebContentProvider(Protocol):
    name: ClassVar[str]

    def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent: ...
