"""Public, provenance-carrying resources that can survive a session restart."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit

EvidenceSource = Literal[
    "user_input",
    "web_search_result",
    "web_fetch_source",
    "web_fetch_link",
    "social_search_result",
    "social_read_source",
]
PlatformHint = Literal["bilibili", "xiaohongshu"]

_SOURCES: frozenset[str] = frozenset(
    {
        "user_input",
        "web_search_result",
        "web_fetch_source",
        "web_fetch_link",
        "social_search_result",
        "social_read_source",
    }
)
_PLATFORM_HINTS: frozenset[str] = frozenset({"bilibili", "xiaohongshu"})
_SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "key",
        "secret",
        "session",
        "session_id",
        "sessionid",
        "sid",
        "sig",
        "signature",
        "token",
        "xsec_token",
    }
)


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


def platform_hint_for_url(url: str) -> PlatformHint | None:
    try:
        host = (urlsplit(normalize_url(url)).hostname or "").lower()
    except ValueError:
        return None
    if host == "bilibili.com" or host.endswith(".bilibili.com"):
        return "bilibili"
    if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"):
        return "xiaohongshu"
    return None


def is_persistable_public_url(url: str) -> bool:
    """Reject query shapes likely to contain credentials from persisted evidence meta."""

    try:
        normalized = normalize_url(url)
    except ValueError:
        return False
    for key, _value in parse_qsl(urlsplit(normalized).query, keep_blank_values=True):
        folded = key.casefold()
        if (
            folded in _SENSITIVE_QUERY_KEYS
            or folded.endswith("_token")
            or folded.endswith("_secret")
        ):
            return False
    return True


@dataclass(frozen=True)
class PublicEvidence:
    evidence_ref: str
    normalized_url: str
    source: EvidenceSource
    source_call_id: str | None = None
    source_event_uuid: str | None = None
    parent_ref: str | None = None
    label: str | None = None
    platform_hint: PlatformHint | None = None

    def to_meta(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "evidence_ref": self.evidence_ref,
            "normalized_url": self.normalized_url,
            "source": self.source,
        }
        if self.source_call_id:
            item["source_call_id"] = self.source_call_id
        if self.parent_ref:
            item["parent_ref"] = self.parent_ref
        if self.label:
            item["label"] = self.label[:160]
        if self.platform_hint:
            item["platform_hint"] = self.platform_hint
        return item

    @classmethod
    def from_meta(
        cls,
        value: object,
        *,
        source_event_uuid: str | None = None,
        require_persistable: bool = True,
    ) -> PublicEvidence | None:
        if not isinstance(value, dict):
            return None
        evidence_ref = value.get("evidence_ref")
        url = value.get("normalized_url")
        source = value.get("source")
        if (
            not isinstance(evidence_ref, str)
            or not evidence_ref.startswith("evidence_ref_")
            or not isinstance(url, str)
            or not isinstance(source, str)
            or source not in _SOURCES
        ):
            return None
        try:
            normalized = normalize_url(url)
        except ValueError:
            return None
        if require_persistable and not is_persistable_public_url(normalized):
            return None
        source_call_id = value.get("source_call_id")
        parent_ref = value.get("parent_ref")
        label = value.get("label")
        platform_hint = value.get("platform_hint")
        if source_call_id is not None and not isinstance(source_call_id, str):
            return None
        if parent_ref is not None and (
            not isinstance(parent_ref, str) or not parent_ref.startswith("evidence_ref_")
        ):
            return None
        if label is not None and not isinstance(label, str):
            return None
        if platform_hint is not None and platform_hint not in _PLATFORM_HINTS:
            return None
        return cls(
            evidence_ref=evidence_ref,
            normalized_url=normalized,
            source=cast(EvidenceSource, source),
            source_call_id=source_call_id,
            source_event_uuid=source_event_uuid,
            parent_ref=parent_ref,
            label=label[:160] if label else None,
            platform_hint=cast(PlatformHint | None, platform_hint),
        )


def make_public_evidence(
    url: str,
    *,
    source: EvidenceSource,
    source_call_id: str | None = None,
    parent_ref: str | None = None,
    label: str | None = None,
) -> PublicEvidence:
    normalized = normalize_url(url)
    return PublicEvidence(
        evidence_ref=f"evidence_ref_{secrets.token_hex(16)}",
        normalized_url=normalized,
        source=source,
        source_call_id=source_call_id,
        parent_ref=parent_ref,
        label=label[:160] if label else None,
        platform_hint=platform_hint_for_url(normalized),
    )


def evidence_result_meta(items: list[PublicEvidence]) -> dict[str, object]:
    """Split runtime-only sensitive URLs from restart-safe public evidence."""

    public_items = [
        item.to_meta() for item in items if is_persistable_public_url(item.normalized_url)
    ]
    ephemeral_items = [
        item.to_meta() for item in items if not is_persistable_public_url(item.normalized_url)
    ]
    result: dict[str, object] = {}
    if public_items:
        result["public_evidence"] = {"version": 1, "items": public_items}
    if ephemeral_items:
        result["ephemeral_evidence"] = {"version": 1, "items": ephemeral_items}
    return result


class PublicEvidenceLedger:
    """Exact-URL allowlist plus provenance, scoped to one persistent session."""

    def __init__(self) -> None:
        self._by_ref: dict[str, PublicEvidence] = {}
        self._by_url: dict[str, PublicEvidence] = {}

    def register(self, evidence: PublicEvidence) -> None:
        self._by_ref[evidence.evidence_ref] = evidence
        self._by_url[evidence.normalized_url] = evidence

    def resolve(self, evidence_ref: str) -> PublicEvidence | None:
        return self._by_ref.get(evidence_ref)

    def find_url(self, url: str) -> PublicEvidence | None:
        try:
            normalized = normalize_url(url)
        except ValueError:
            return None
        return self._by_url.get(normalized)

    def entries(self) -> tuple[PublicEvidence, ...]:
        return tuple(self._by_ref.values())
