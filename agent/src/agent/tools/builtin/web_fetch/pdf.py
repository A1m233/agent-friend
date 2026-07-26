"""Bounded PDF download and text-layer extraction."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import httpx
from pypdf import PdfReader

from .provider import (
    ExtractedContent,
    WebContentEmptyError,
    WebContentNetworkError,
    WebContentResourceLimitError,
    WebContentUnsupportedError,
)
from .url_safety import UnsafeUrlError, resolve_redirect, validate_public_url

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGES = 100
MAX_PAGE_STREAM_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_REDIRECTS = 5


def download_and_extract_pdf(
    url: str, *, focus: str | None = None
) -> tuple[ExtractedContent, dict[str, Any]]:
    requested = validate_public_url(url)
    current = requested
    data = b""
    timeout = httpx.Timeout(20.0, connect=10.0, read=20.0, write=10.0, pool=5.0)
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
    try:
        with httpx.Client(
            timeout=timeout, limits=limits, follow_redirects=False, trust_env=False
        ) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                with client.stream(
                    "GET", current, headers={"Accept": "application/pdf"}
                ) as response:
                    if response.is_redirect:
                        if redirect_count >= MAX_REDIRECTS:
                            raise WebContentNetworkError("too many redirects")
                        current = resolve_redirect(current, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_DOWNLOAD_BYTES:
                            raise WebContentResourceLimitError("PDF exceeds download limit")
                    data = bytes(content)
                    break
    except UnsafeUrlError:
        raise
    except (WebContentResourceLimitError, WebContentUnsupportedError):
        raise
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
        raise WebContentNetworkError(str(exc)) from exc
    if not data.startswith(b"%PDF-"):
        raise WebContentUnsupportedError("response is not a PDF")
    text, page_count, skipped_pages, truncated = extract_pdf_text(data, focus=focus)
    return (
        ExtractedContent(requested, current, text, "pdf"),
        {
            "download_bytes": len(data),
            "page_count": page_count,
            "skipped_pages": skipped_pages,
            "truncated": truncated,
        },
    )


def extract_pdf_text(data: bytes, *, focus: str | None = None) -> tuple[str, int, int, bool]:
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise WebContentUnsupportedError("PDF cannot be parsed") from exc
    parts: list[tuple[int, str]] = []
    skipped_pages = 0
    resource_limited_pages = 0
    truncated = len(reader.pages) > MAX_PAGES
    total_chars = 0
    for page_number, page in enumerate(reader.pages[:MAX_PAGES], 1):
        try:
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > MAX_PAGE_STREAM_BYTES:
                skipped_pages += 1
                resource_limited_pages += 1
                continue
            text = (page.extract_text() or "").strip()
        except Exception:
            skipped_pages += 1
            continue
        if not text:
            continue
        remaining = MAX_TEXT_CHARS - total_chars
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        parts.append((page_number, text))
        total_chars += len(text)
    if not parts:
        if resource_limited_pages:
            raise WebContentResourceLimitError("PDF page stream exceeds extraction limit")
        raise WebContentEmptyError("PDF has no extractable text layer")
    if focus:
        terms = {item.lower() for item in focus.split() if item.strip()}
        parts.sort(key=lambda item: (-sum(item[1].lower().count(term) for term in terms), item[0]))
    rendered = "\n\n".join(f"[page {number}]\n{text}" for number, text in parts)
    return rendered, len(reader.pages), skipped_pages, truncated
