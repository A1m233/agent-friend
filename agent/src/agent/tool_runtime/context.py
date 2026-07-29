"""Trusted values that exist only for one tool-running turn."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}，。；：！？）】》"


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL 必须是绝对 http/https 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 不能包含 userinfo")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    port = parsed.port
    if port is not None and port not in {80, 443}:
        raise ValueError("URL 只允许 80/443 端口")
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


@dataclass(frozen=True)
class UrlAuthorization:
    normalized_url: str
    source: Literal["user_input", "web_search_result"]
    source_call_id: str | None = None


@dataclass
class UrlAuthorizationLedger:
    _entries: dict[str, UrlAuthorization] = field(default_factory=dict)

    def register(
        self,
        url: str,
        source: Literal["user_input", "web_search_result"],
        source_call_id: str | None = None,
    ) -> None:
        normalized = normalize_url(url)
        self._entries[normalized] = UrlAuthorization(normalized, source, source_call_id)

    def register_user_text(self, text: str) -> None:
        for match in _URL_PATTERN.findall(text):
            candidate = match.rstrip(_TRAILING_PUNCTUATION)
            try:
                self.register(candidate, "user_input")
            except ValueError:
                continue

    def authorize(self, url: str) -> UrlAuthorization | None:
        try:
            normalized = normalize_url(url)
        except ValueError:
            return None
        return self._entries.get(normalized)


@dataclass
class ToolRunContext:
    run_id: str
    trusted_now: datetime
    trusted_timezone: str
    url_authorizations: UrlAuthorizationLedger = field(default_factory=UrlAuthorizationLedger)

    @classmethod
    def create(cls, user_input: str, *, now: datetime | None = None) -> ToolRunContext:
        trusted_now = now or datetime.now().astimezone()
        if trusted_now.tzinfo is None:
            raise ValueError("trusted now must be timezone-aware")
        zone = getattr(trusted_now.tzinfo, "key", None) or _offset_text(trusted_now)
        ledger = UrlAuthorizationLedger()
        ledger.register_user_text(user_input)
        return cls(f"toolrun_{uuid4().hex}", trusted_now, str(zone), ledger)

    def register_tool_result(self, name: str, call_id: str, source_urls: object) -> None:
        if name != "web_search" or not isinstance(source_urls, list):
            return
        for url in source_urls:
            if not isinstance(url, str):
                continue
            try:
                self.url_authorizations.register(url, "web_search_result", call_id)
            except ValueError:
                continue

    def render_for_model(self) -> str:
        """Render the same trusted clock snapshot that hidden tool args receive.

        The block becomes part of the append-only model-context snapshot near the current
        input.  Keeping it on this object prevents the model-visible time and tool-visible
        time from drifting apart during a multi-call turn.
        """
        current_datetime = self.trusted_now.isoformat(timespec="seconds")
        today = self.trusted_now.date()
        return (
            f'<runtime_now at="{current_datetime}" tz="{self.trusted_timezone}" '
            f'yesterday="{today - timedelta(days=1)}" today="{today}" '
            f'tomorrow="{today + timedelta(days=1)}" />\n'
            "本轮时间以此为准；日历锚点同样可信，历史日期不覆盖，勿反推。"
        )


def _offset_text(value: datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        return "UTC"
    total = int(offset.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    hours, minutes = divmod(abs(total), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"
