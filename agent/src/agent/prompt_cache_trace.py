"""Content-free prompt-cache request fingerprints and cross-call lineage."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

from .context import CHARS_TO_TOKENS

PROCESS_INSTANCE_ID = f"{os.getpid()}-{uuid4().hex[:12]}"
_FINGERPRINT_VERSION = 1
_DIGEST_CHARS = 24


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(label: str, value: Any) -> str:
    payload = _canonical_json(value).encode("utf-8")
    digest = sha256(
        f"agent-friend:{label}:v{_FINGERPRINT_VERSION}\0".encode() + payload
    ).hexdigest()
    return digest[:_DIGEST_CHARS]


def _rendered_chars(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    return len(_canonical_json(value))


@dataclass(frozen=True)
class PromptRequestTrace:
    """One outbound prompt snapshot without retaining message content."""

    namespace: str
    process_instance_id: str
    prompt_fingerprint: str
    system_fingerprint: str
    tool_schema_fingerprint: str
    message_digests: tuple[str, ...]
    message_chars: tuple[int, ...]
    message_roles: tuple[str, ...]
    prompt_payload_bytes: int
    role_counts: dict[str, int]
    total_message_chars: int
    tool_result_count: int
    tool_result_chars: int
    max_tool_result_chars: int
    lineage_state: str
    cache_idle_ms: int | None
    previous_cache_state: str | None
    previous_request_prefix_preserved: bool | None
    common_prefix_messages: int | None
    common_prefix_estimated_tokens: int | None
    first_divergence_index: int | None
    first_divergence_role: str | None
    tool_schema_preserved: bool | None

    def log_fields(self) -> dict[str, Any]:
        """Return compact, parseable fields suitable for ``run_latency`` logs."""
        return {
            "cache_namespace": self.namespace,
            "process_instance_id": self.process_instance_id,
            "prompt_fingerprint": self.prompt_fingerprint,
            "system_fingerprint": self.system_fingerprint,
            "tool_schema_fingerprint": self.tool_schema_fingerprint,
            "prompt_payload_bytes": self.prompt_payload_bytes,
            "message_chars": self.total_message_chars,
            "system_messages": self.role_counts.get("system", 0),
            "user_messages": self.role_counts.get("user", 0),
            "assistant_messages": self.role_counts.get("assistant", 0),
            "tool_messages": self.role_counts.get("tool", 0),
            "tool_result_chars": self.tool_result_chars,
            "max_tool_result_chars": self.max_tool_result_chars,
            "cache_lineage": self.lineage_state,
            "cache_idle_ms": self.cache_idle_ms,
            "previous_cache_state": self.previous_cache_state,
            "previous_request_prefix_preserved": self.previous_request_prefix_preserved,
            "common_prefix_messages": self.common_prefix_messages,
            "common_prefix_estimated_tokens": self.common_prefix_estimated_tokens,
            "first_divergence_index": self.first_divergence_index,
            "first_divergence_role": self.first_divergence_role,
            "tool_schema_preserved": self.tool_schema_preserved,
        }


@dataclass
class _PreviousRequest:
    message_digests: tuple[str, ...]
    message_chars: tuple[int, ...]
    message_roles: tuple[str, ...]
    tool_schema_fingerprint: str
    finished_at_unix_ms: int
    cache_state: str


@dataclass(frozen=True)
class _RestoredLineage:
    finished_at_unix_ms: int
    cache_state: str


class PromptCacheTraceTracker:
    """Track the previous request for each provider/model/cache-key namespace."""

    def __init__(self) -> None:
        self._previous: dict[str, _PreviousRequest] = {}
        self._restored: dict[str, _RestoredLineage] = {}

    def restore(self, value: Any, *, finished_at_unix_ms: int) -> None:
        """Restore durable cache lineage without restoring content-derived digests."""
        if not isinstance(value, dict):
            return
        namespace = value.get("cache_namespace")
        cache_state = value.get("cache_state")
        if not isinstance(namespace, str) or not namespace:
            return
        self._restored.setdefault(
            namespace,
            _RestoredLineage(
                finished_at_unix_ms=finished_at_unix_ms,
                cache_state=cache_state if isinstance(cache_state, str) else "unavailable",
            ),
        )

    def begin(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        provider_model: str,
        api_base: str | None,
        prompt_cache_key: str | None,
    ) -> PromptRequestTrace:
        namespace = _fingerprint(
            "cache-namespace",
            {
                "provider_model": provider_model,
                "api_base": api_base or "default",
                "prompt_cache_key": prompt_cache_key or "none",
            },
        )
        message_digests = tuple(_fingerprint("message", message) for message in messages)
        message_chars = tuple(_rendered_chars(message) for message in messages)
        message_roles = tuple(str(message.get("role", "unknown")) for message in messages)
        role_counts = dict(Counter(message_roles))
        system_messages = [
            message for message in messages if str(message.get("role", "")) == "system"
        ]
        tool_messages = [message for message in messages if str(message.get("role", "")) == "tool"]
        tool_result_chars = sum(
            _rendered_chars(message.get("content")) for message in tool_messages
        )
        max_tool_result_chars = max(
            (_rendered_chars(message.get("content")) for message in tool_messages),
            default=0,
        )
        tool_schema_fingerprint = _fingerprint("tool-schema", tools or [])
        prompt_payload = {"messages": messages, "tools": tools or []}

        now_ms = int(time.time() * 1000)
        previous = self._previous.get(namespace)
        restored = self._restored.get(namespace)
        if previous is not None:
            lineage_state = "continuous"
            cache_idle_ms = max(0, now_ms - previous.finished_at_unix_ms)
            previous_cache_state = previous.cache_state
            common = 0
            for old_digest, new_digest in zip(
                previous.message_digests, message_digests, strict=False
            ):
                if old_digest != new_digest:
                    break
                common += 1
            tool_schema_preserved = previous.tool_schema_fingerprint == tool_schema_fingerprint
            prefix_preserved = common == len(previous.message_digests) and tool_schema_preserved
            common_chars = sum(message_chars[:common])
            common_tokens = int(common_chars * CHARS_TO_TOKENS)
            divergence_index = common if common < len(previous.message_digests) else None
            divergence_role = (
                message_roles[common]
                if divergence_index is not None and common < len(message_roles)
                else previous.message_roles[common]
                if divergence_index is not None and common < len(previous.message_roles)
                else None
            )
        elif restored is not None:
            lineage_state = "restored_without_prefix"
            cache_idle_ms = max(0, now_ms - restored.finished_at_unix_ms)
            previous_cache_state = restored.cache_state
            prefix_preserved = None
            common = None
            common_tokens = None
            divergence_index = None
            divergence_role = None
            tool_schema_preserved = None
        else:
            lineage_state = "new"
            cache_idle_ms = None
            previous_cache_state = None
            prefix_preserved = None
            common = None
            common_tokens = None
            divergence_index = None
            divergence_role = None
            tool_schema_preserved = None

        return PromptRequestTrace(
            namespace=namespace,
            process_instance_id=PROCESS_INSTANCE_ID,
            prompt_fingerprint=_fingerprint("prompt", prompt_payload),
            system_fingerprint=_fingerprint("system", system_messages),
            tool_schema_fingerprint=tool_schema_fingerprint,
            message_digests=message_digests,
            message_chars=message_chars,
            message_roles=message_roles,
            prompt_payload_bytes=len(_canonical_json(prompt_payload).encode("utf-8")),
            role_counts=role_counts,
            total_message_chars=sum(message_chars),
            tool_result_count=len(tool_messages),
            tool_result_chars=tool_result_chars,
            max_tool_result_chars=max_tool_result_chars,
            lineage_state=lineage_state,
            cache_idle_ms=cache_idle_ms,
            previous_cache_state=previous_cache_state,
            previous_request_prefix_preserved=prefix_preserved,
            common_prefix_messages=common,
            common_prefix_estimated_tokens=common_tokens,
            first_divergence_index=divergence_index,
            first_divergence_role=divergence_role,
            tool_schema_preserved=tool_schema_preserved,
        )

    def finish(self, trace: PromptRequestTrace, *, cache_state: str) -> dict[str, Any]:
        """Advance lineage and return the durable, content-free summary."""
        finished_at_unix_ms = int(time.time() * 1000)
        self._previous[trace.namespace] = _PreviousRequest(
            message_digests=trace.message_digests,
            message_chars=trace.message_chars,
            message_roles=trace.message_roles,
            tool_schema_fingerprint=trace.tool_schema_fingerprint,
            finished_at_unix_ms=finished_at_unix_ms,
            cache_state=cache_state,
        )
        self._restored.pop(trace.namespace, None)
        return {
            "version": _FINGERPRINT_VERSION,
            "cache_namespace": trace.namespace,
            "process_instance_id": trace.process_instance_id,
            "prompt_fingerprint": trace.prompt_fingerprint,
            "system_fingerprint": trace.system_fingerprint,
            "tool_schema_fingerprint": trace.tool_schema_fingerprint,
            "cache_state": cache_state,
            "cache_lineage": trace.lineage_state,
            "cache_idle_ms": trace.cache_idle_ms,
            "previous_cache_state": trace.previous_cache_state,
            "previous_request_prefix_preserved": trace.previous_request_prefix_preserved,
            "common_prefix_messages": trace.common_prefix_messages,
            "common_prefix_estimated_tokens": trace.common_prefix_estimated_tokens,
            "first_divergence_index": trace.first_divergence_index,
            "first_divergence_role": trace.first_divergence_role,
            "tool_schema_preserved": trace.tool_schema_preserved,
            "prompt_payload_bytes": trace.prompt_payload_bytes,
            "message_chars": trace.total_message_chars,
            "role_counts": dict(trace.role_counts),
            "tool_result_count": trace.tool_result_count,
            "tool_result_chars": trace.tool_result_chars,
            "max_tool_result_chars": trace.max_tool_result_chars,
            "finished_at_unix_ms": finished_at_unix_ms,
        }
