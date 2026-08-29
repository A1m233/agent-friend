"""Content-free witness of the final provider request body.

The witness is built from the fully prepared HTTP request immediately before it
is sent.  It intentionally retains only cryptographic digests and aggregate
sizes; request/message/tool content never leaves the request hook.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, Literal

import httpx

_WITNESS_VERSION = 2
_LOG_DIGEST_CHARS = 24

ProviderRequestRelation = Literal[
    "baseline",
    "same_request_retry",
    "strict_extension",
    "rebase",
    "unavailable",
]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(label: str, payload: bytes) -> str:
    return sha256(f"agent-friend:{label}:v{_WITNESS_VERSION}\0".encode() + payload).hexdigest()


def _item_role(item: Any) -> str:
    if not isinstance(item, dict):
        return "unknown"
    for name in ("role", "type"):
        value = item.get(name)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _item_domain(item: Any, role: str) -> str:
    # Ordinary user content has agent-friend markers escaped before provider
    # rendering.  Only the trusted host packet retains this exact marker.
    rendered = _canonical_json_bytes(item)
    if b"<agent_friend_runtime_context" in rendered:
        return "runtime"
    if role in {"system", "developer", "instructions"}:
        return "system"
    return "messages"


@dataclass(frozen=True, repr=False)
class ProviderRequestWitness:
    """Opaque, content-free identity of one final provider request."""

    provider_id: str
    transport: str
    transform_identity: str
    transcript_field: str | None
    transcript_item_digests: tuple[str, ...]
    transcript_item_roles: tuple[str, ...]
    transcript_item_domains: tuple[str, ...]
    envelope_digest: str | None
    request_digest: str
    request_bytes: int
    contributor_mapping_precision: str = "generic"
    version: int = _WITNESS_VERSION

    def __repr__(self) -> str:
        return (
            "ProviderRequestWitness("
            f"provider_id={self.provider_id!r}, transport={self.transport!r}, "
            f"transform_identity={self.transform_identity!r}, "
            f"transcript_field={self.transcript_field!r}, "
            f"transcript_items={len(self.transcript_item_digests)}, "
            f"request_bytes={self.request_bytes}, "
            f"request_fingerprint={self.request_fingerprint!r})"
        )

    @property
    def request_fingerprint(self) -> str:
        return self.request_digest[:_LOG_DIGEST_CHARS]

    def relation_to(self, previous: ProviderRequestWitness | None) -> ProviderRequestRelation:
        if previous is None:
            return "baseline"
        if (
            self.version != previous.version
            or self.provider_id != previous.provider_id
            or self.transport != previous.transport
            or self.transform_identity != previous.transform_identity
        ):
            return "rebase"
        if self.request_digest == previous.request_digest:
            return "same_request_retry"
        if (
            self.transcript_field is None
            or self.transcript_field != previous.transcript_field
            or self.envelope_digest is None
            or self.envelope_digest != previous.envelope_digest
        ):
            return "rebase"
        old = previous.transcript_item_digests
        new = self.transcript_item_digests
        if len(new) > len(old) and new[: len(old)] == old:
            return "strict_extension"
        return "rebase"

    def common_prefix_items(self, previous: ProviderRequestWitness | None) -> int | None:
        if previous is None or self.transcript_field != previous.transcript_field:
            return None
        common = 0
        for old_item, new_item in zip(
            previous.transcript_item_digests,
            self.transcript_item_digests,
            strict=False,
        ):
            if old_item != new_item:
                break
            common += 1
        return common

    def first_divergence_index(self, previous: ProviderRequestWitness | None) -> int | None:
        common = self.common_prefix_items(previous)
        if previous is None or common is None:
            return None
        if common == len(previous.transcript_item_digests) and common == len(
            self.transcript_item_digests
        ):
            return None
        return common

    def first_divergence_role(self, previous: ProviderRequestWitness | None) -> str | None:
        index = self.first_divergence_index(previous)
        if previous is None or index is None:
            return None
        if index < len(self.transcript_item_roles):
            return self.transcript_item_roles[index]
        if index < len(previous.transcript_item_roles):
            return previous.transcript_item_roles[index]
        return None

    def first_divergence_domain(self, previous: ProviderRequestWitness | None) -> str | None:
        if previous is not None and (
            self.version != previous.version
            or self.provider_id != previous.provider_id
            or self.transport != previous.transport
            or self.transform_identity != previous.transform_identity
        ):
            return "model-route"
        if (
            previous is not None
            and self.envelope_digest is not None
            and previous.envelope_digest is not None
            and self.envelope_digest != previous.envelope_digest
        ):
            return "settings"
        previous_domain = self.first_divergence_previous_domain(previous)
        current_domain = self.first_divergence_current_domain(previous)
        if "memory" in {previous_domain, current_domain}:
            return "memory"
        return current_domain or previous_domain

    def first_divergence_previous_domain(
        self,
        previous: ProviderRequestWitness | None,
    ) -> str | None:
        index = self.first_divergence_index(previous)
        if previous is None or index is None:
            return None
        if index < len(previous.transcript_item_domains):
            return previous.transcript_item_domains[index]
        return None

    def first_divergence_current_domain(
        self,
        previous: ProviderRequestWitness | None,
    ) -> str | None:
        index = self.first_divergence_index(previous)
        if previous is None or index is None:
            return None
        if index < len(self.transcript_item_domains):
            return self.transcript_item_domains[index]
        return None

    def lost_prefix_items(self, previous: ProviderRequestWitness | None) -> int | None:
        common = self.common_prefix_items(previous)
        if previous is None or common is None:
            return None
        return max(0, len(previous.transcript_item_digests) - common)


def witness_from_body(
    body: bytes,
    *,
    provider_id: str,
    transport: str,
    transform_identity: str,
) -> ProviderRequestWitness:
    """Build a witness while retaining no request content."""

    transcript_field: str | None = None
    transcript_item_digests: tuple[str, ...] = ()
    transcript_item_roles: tuple[str, ...] = ()
    transcript_item_domains: tuple[str, ...] = ()
    envelope_digest: str | None = None
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        for candidate in ("messages", "input"):
            value = parsed.get(candidate)
            if isinstance(value, list):
                transcript_field = candidate
                transcript_item_digests = tuple(
                    _digest("provider-transcript-item", _canonical_json_bytes(item))
                    for item in value
                )
                transcript_item_roles = tuple(_item_role(item) for item in value)
                transcript_item_domains = tuple(
                    _item_domain(item, role)
                    for item, role in zip(value, transcript_item_roles, strict=True)
                )
                envelope = dict(parsed)
                envelope.pop(candidate, None)
                envelope_digest = _digest(
                    "provider-request-envelope",
                    _canonical_json_bytes(envelope),
                )
                break
    return ProviderRequestWitness(
        provider_id=provider_id,
        transport=transport,
        transform_identity=transform_identity,
        transcript_field=transcript_field,
        transcript_item_digests=transcript_item_digests,
        transcript_item_roles=transcript_item_roles,
        transcript_item_domains=transcript_item_domains,
        envelope_digest=envelope_digest,
        request_digest=_digest("provider-request-body", body),
        request_bytes=len(body),
    )


class ProviderRequestCapture:
    """Per-call HTTPX hook target; safe to use across one request thread/task."""

    def __init__(
        self,
        *,
        provider_id: str,
        transport: str,
        transform_identity: str,
        transcript_items: list[dict[str, Any]] | None = None,
        transcript_item_domains: tuple[str, ...] | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._transport = transport
        self._transform_identity = transform_identity
        self._transcript_item_domains = transcript_item_domains
        self._source_item_digests = (
            tuple(
                _digest("provider-transcript-item", _canonical_json_bytes(item))
                for item in transcript_items
            )
            if transcript_items is not None and transcript_item_domains is not None
            else None
        )
        self.witness: ProviderRequestWitness | None = None

    def observe(self, request: httpx.Request) -> None:
        witness = witness_from_body(
            request.content,
            provider_id=self._provider_id,
            transport=self._transport,
            transform_identity=self._transform_identity,
        )
        domains = self._transcript_item_domains
        source_digests = self._source_item_digests
        if domains is not None and source_digests is not None:
            if (
                len(domains) == len(source_digests)
                and witness.transcript_item_digests == source_digests
            ):
                witness = replace(
                    witness,
                    transcript_item_domains=domains,
                    contributor_mapping_precision="exact_item",
                )
            else:
                witness = replace(witness, contributor_mapping_precision="unavailable")
        self.witness = witness

    async def observe_async(self, request: httpx.Request) -> None:
        self.observe(request)


__all__ = [
    "ProviderRequestCapture",
    "ProviderRequestRelation",
    "ProviderRequestWitness",
    "witness_from_body",
]
