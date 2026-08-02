"""One-request, bounded static HTML download and local extraction."""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html
from trafilatura import extract

from ....tool_runtime.evidence import normalize_url
from .provider import (
    ExtractedContent,
    ExtractedLink,
    WebContentEmptyError,
    WebContentNetworkError,
    WebContentResourceLimitError,
    WebContentUnsupportedError,
)
from .url_safety import UnsafeUrlError, resolve_redirect, validate_public_url

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_LINKS = 50
MAX_REDIRECTS = 5
_HTML_CONTENT_TYPES = {"application/xhtml+xml", "text/html"}
_SPACE = re.compile(r"\s+")


class SafeHtmlReader:
    """Fetch exactly one public page, then extract body and links from those bytes."""

    name = "local_static_html"

    def open(self, url: str, *, focus: str | None = None) -> ExtractedContent:
        del focus  # Local extraction is deterministic; model-visible focus is advisory only.
        requested = validate_public_url(url)
        current = requested
        data = b""
        content_type = ""
        timeout = httpx.Timeout(20.0, connect=10.0, read=20.0, write=10.0, pool=5.0)
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
        try:
            with httpx.Client(
                timeout=timeout,
                limits=limits,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                for redirect_count in range(MAX_REDIRECTS + 1):
                    with client.stream(
                        "GET",
                        current,
                        headers={
                            "Accept": "text/html,application/xhtml+xml",
                            "User-Agent": "agent-friend/1.0 static-page-reader",
                        },
                    ) as response:
                        if response.is_redirect:
                            if redirect_count >= MAX_REDIRECTS:
                                raise WebContentNetworkError("too many redirects")
                            current = resolve_redirect(
                                current, response.headers.get("location", "")
                            )
                            continue
                        response.raise_for_status()
                        content_type = (
                            response.headers.get("content-type", "").split(";", 1)[0].lower()
                        )
                        chunks = bytearray()
                        for chunk in response.iter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > MAX_DOWNLOAD_BYTES:
                                raise WebContentResourceLimitError("HTML exceeds download limit")
                        data = bytes(chunks)
                        break
        except UnsafeUrlError:
            raise
        except WebContentResourceLimitError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            raise WebContentNetworkError(str(exc)) from exc

        sniff = data[:512].lstrip().lower()
        if content_type not in _HTML_CONTENT_TYPES and not (
            sniff.startswith(b"<!doctype html") or sniff.startswith(b"<html")
        ):
            raise WebContentUnsupportedError("response is not HTML")
        if not data:
            raise WebContentEmptyError("page is empty")
        return extract_static_html(data, requested_url=requested, final_url=current)


def extract_static_html(
    data: bytes | str,
    *,
    requested_url: str,
    final_url: str,
) -> ExtractedContent:
    """Extract main Markdown and anchor links without performing network I/O."""

    body = (
        extract(
            data,
            url=final_url,
            output_format="markdown",
            include_comments=False,
            include_links=False,
            include_images=False,
        )
        or ""
    ).strip()
    try:
        document = lxml_html.fromstring(data, base_url=final_url)
    except (TypeError, ValueError) as exc:
        raise WebContentUnsupportedError("HTML cannot be parsed") from exc

    links: list[ExtractedLink] = []
    seen: set[str] = set()
    link_total = 0
    for element in document.iter("a"):
        href = element.get("href")
        if not href:
            continue
        try:
            normalized = normalize_url(urljoin(final_url, href))
        except (TypeError, ValueError):
            continue
        if normalized == final_url or normalized in seen:
            continue
        seen.add(normalized)
        link_total += 1
        if len(links) >= MAX_LINKS:
            continue
        label = _SPACE.sub(" ", element.text_content()).strip()
        if not label:
            label = _SPACE.sub(" ", element.get("aria-label") or element.get("title") or "").strip()
        links.append(ExtractedLink(normalized, label[:160] or None))

    if not body and not links:
        raise WebContentEmptyError("page has no extractable body or links")
    return ExtractedContent(
        requested_url=requested_url,
        final_url=final_url,
        text=body,
        content_kind="html",
        links=tuple(links),
        link_total=link_total,
        links_truncated=link_total > len(links),
    )
