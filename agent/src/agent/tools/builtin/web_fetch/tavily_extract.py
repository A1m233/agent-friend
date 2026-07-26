"""Tavily Extract adapter for regular webpages."""

from __future__ import annotations

from typing import Any, ClassVar

from .provider import (
    ExtractedContent,
    WebContentAuthError,
    WebContentEmptyError,
    WebContentError,
    WebContentNetworkError,
    WebContentRateLimitError,
)


class TavilyExtractProvider:
    name: ClassVar[str] = "tavily_extract"

    def __init__(self, api_key: str, *, timeout_seconds: float = 30.0) -> None:
        if not api_key:
            raise WebContentAuthError("Tavily key is empty")
        self._api_key = api_key
        self._timeout_seconds = min(60.0, max(1.0, timeout_seconds))

    def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent:
        from tavily import TavilyClient
        from tavily.errors import (
            ForbiddenError,
            InvalidAPIKeyError,
            MissingAPIKeyError,
            UsageLimitExceededError,
        )
        from tavily.errors import TimeoutError as TavilyTimeoutError

        try:
            response: dict[str, Any] = TavilyClient(api_key=self._api_key).extract(
                urls=[url],
                format="markdown",
                timeout=self._timeout_seconds,
                query=focus or None,
            )
        except (InvalidAPIKeyError, MissingAPIKeyError) as exc:
            raise WebContentAuthError(str(exc)) from exc
        except (UsageLimitExceededError, ForbiddenError) as exc:
            raise WebContentRateLimitError(str(exc)) from exc
        except TavilyTimeoutError as exc:
            raise WebContentNetworkError(str(exc)) from exc
        except Exception as exc:
            message = str(exc).lower()
            if any(word in message for word in ("timeout", "connect", "network", "dns")):
                raise WebContentNetworkError(str(exc)) from exc
            raise WebContentError(str(exc)) from exc

        results = response.get("results") or []
        if not results:
            failed = response.get("failed_results") or []
            detail = failed[0].get("error", "") if failed and isinstance(failed[0], dict) else ""
            raise WebContentEmptyError(detail or "provider returned no content")
        result = results[0]
        text = str(result.get("raw_content") or result.get("content") or "").strip()
        if not text:
            raise WebContentEmptyError("provider returned empty content")
        return ExtractedContent(
            requested_url=url,
            final_url=str(result.get("url") or url),
            text=text,
            content_kind="html",
        )
