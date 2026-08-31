"""Content-free prompt-cache request fingerprints and cross-call lineage."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

from llm_providers import ProviderRequestWitness

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


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _is_runtime_message(message: dict[str, Any]) -> bool:
    rendered = _canonical_json(message)
    return any(
        marker in rendered
        for marker in (
            "<agent_friend_runtime_context",
            "<agent_friend_environment_context",
            "<agent_friend_channel_context",
            "<agent_friend_permission_context",
            "<agent_friend_runtime_capabilities",
            "<agent_friend_capability_catalog",
            "<agent_friend_host_advisory",
        )
    )


def _position_bucket(positions: tuple[int, ...], total_items: int) -> str:
    if not positions:
        return "none"
    first = positions[0]
    if first <= 1:
        return "head"
    if positions[-1] >= max(0, total_items - 2):
        return "tail"
    return "middle"


def _optional_changed(previous: str | None, current: str | None) -> bool:
    return previous != current


def _exact_change_flags(
    previous: PromptRequestTrace,
    current: PromptRequestTrace,
) -> dict[str, bool | None]:
    previous_core = previous.core_message_digests
    current_core = current.core_message_digests
    core_prefix_preserved = (
        len(current_core) >= len(previous_core)
        and current_core[: len(previous_core)] == previous_core
    )
    messages_appended = core_prefix_preserved and len(current_core) > len(previous_core)
    messages_changed = previous_core != current_core
    previous_memory_present = bool(previous.memory_item_digests)
    current_memory_present = bool(current.memory_item_digests)
    return {
        "system_changed": previous.system_fingerprint != current.system_fingerprint,
        "persona_changed": _optional_changed(
            previous.persona_fingerprint,
            current.persona_fingerprint,
        ),
        "tools_changed": (previous.tool_schema_fingerprint != current.tool_schema_fingerprint),
        "skills_changed": previous.skill_fingerprint != current.skill_fingerprint,
        "messages_changed": messages_changed,
        "messages_appended": messages_appended,
        "messages_rewritten": messages_changed and not core_prefix_preserved,
        "runtime_changed": previous.runtime_fingerprint != current.runtime_fingerprint,
        "settings_changed": previous.settings_fingerprint != current.settings_fingerprint,
        "model_route_changed": (
            previous.model_route_fingerprint != current.model_route_fingerprint
        ),
        "memory_presence_changed": previous_memory_present != current_memory_present,
        "memory_content_changed": (
            previous_memory_present
            and current_memory_present
            and previous.memory_item_digests != current.memory_item_digests
        ),
        "memory_position_changed": (
            previous_memory_present
            and current_memory_present
            and previous.memory_positions != current.memory_positions
        ),
    }


def _coarse_change_flags(
    previous: _RestoredCrossTurn,
    current: PromptRequestTrace,
) -> dict[str, bool | None]:
    current_core_fingerprint = _fingerprint(
        "core-message-sequence",
        current.core_message_digests,
    )
    current_memory_present = bool(current.memory_item_digests)
    return {
        "system_changed": previous.system_fingerprint != current.system_fingerprint,
        "persona_changed": previous.persona_fingerprint != current.persona_fingerprint,
        "tools_changed": (previous.tool_schema_fingerprint != current.tool_schema_fingerprint),
        "skills_changed": previous.skill_fingerprint != current.skill_fingerprint,
        "messages_changed": (previous.core_messages_fingerprint != current_core_fingerprint),
        # A process-resume fingerprint cannot prove whether the old item sequence was
        # retained at the front, so do not manufacture append/rewrite precision.
        "messages_appended": None,
        "messages_rewritten": None,
        "runtime_changed": previous.runtime_fingerprint != current.runtime_fingerprint,
        "settings_changed": previous.settings_fingerprint != current.settings_fingerprint,
        "model_route_changed": (
            previous.model_route_fingerprint != current.model_route_fingerprint
        ),
        "memory_presence_changed": (
            previous.memory_present != current_memory_present
            if previous.memory_present is not None
            else None
        ),
        "memory_content_changed": None,
        "memory_position_changed": (
            previous.memory_present is True
            and current_memory_present
            and previous.memory_position_bucket is not None
            and previous.memory_position_bucket != current.memory_position_bucket
        )
        if previous.memory_present is not None
        else None,
    }


_BREAKPOINT_PRIORITY = (
    ("model_route_changed", "model-route"),
    ("settings_changed", "settings"),
    ("persona_changed", "persona"),
    ("system_changed", "system"),
    ("skills_changed", "tools"),
    ("tools_changed", "tools"),
    ("memory_presence_changed", "memory"),
    ("memory_content_changed", "memory"),
    ("memory_position_changed", "memory"),
    ("messages_rewritten", "messages"),
    ("runtime_changed", "runtime"),
)


def _first_changed_domain(flags: Mapping[str, bool | None]) -> str | None:
    for flag, domain in _BREAKPOINT_PRIORITY:
        if flags.get(flag) is True:
            return domain
    if flags.get("messages_changed") is True and flags.get("messages_appended") is not True:
        return "messages"
    return None


def _has_changed_domain(flags: Mapping[str, bool | None]) -> bool:
    return _first_changed_domain(flags) is not None


@dataclass(frozen=True)
class PromptRequestTrace:
    """One outbound prompt snapshot without retaining message content."""

    namespace: str
    process_instance_id: str
    prompt_fingerprint: str
    system_fingerprint: str
    tool_schema_fingerprint: str
    skill_fingerprint: str
    persona_fingerprint: str | None
    runtime_fingerprint: str
    settings_fingerprint: str
    model_route_fingerprint: str
    message_digests: tuple[str, ...]
    core_message_digests: tuple[str, ...]
    message_chars: tuple[int, ...]
    message_roles: tuple[str, ...]
    message_domains: tuple[str, ...]
    memory_item_digests: tuple[str, ...]
    memory_positions: tuple[int, ...]
    memory_position_bucket: str
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


@dataclass(frozen=True)
class PromptCacheComparison:
    """One content-free comparison binding for the next provider request."""

    scope: str
    precision: str
    previous_witness: ProviderRequestWitness | None = None
    previous_item_count: int | None = None
    previous_input_tokens: int | None = None
    previous_response_model: str | None = None
    first_divergence_domain: str | None = None
    first_divergence_index: int | None = None
    first_divergence_role: str | None = None
    coarse_relation: str | None = None
    change_flags: Mapping[str, bool | None] | None = None

    def observation_fields(self) -> dict[str, Any]:
        return {
            "previous_item_count": self.previous_item_count,
            "previous_input_tokens": self.previous_input_tokens,
            "previous_response_model": self.previous_response_model,
            "first_divergence_domain": self.first_divergence_domain,
            "first_divergence_index": self.first_divergence_index,
            "first_divergence_role": self.first_divergence_role,
            "coarse_relation": self.coarse_relation,
            "change_flags": dict(self.change_flags or {}),
        }


@dataclass
class _PreviousRequest:
    trace: PromptRequestTrace
    run_id: str | None
    witness: ProviderRequestWitness | None
    input_tokens: int | None
    response_model: str | None
    finished_at_unix_ms: int
    cache_state: str


@dataclass(frozen=True)
class _RestoredLineage:
    finished_at_unix_ms: int
    cache_state: str


@dataclass(frozen=True)
class _RestoredCrossTurn:
    system_fingerprint: str | None
    persona_fingerprint: str | None
    tool_schema_fingerprint: str | None
    skill_fingerprint: str | None
    runtime_fingerprint: str | None
    settings_fingerprint: str | None
    model_route_fingerprint: str | None
    core_messages_fingerprint: str | None
    core_message_count: int | None
    transcript_item_count: int | None
    input_tokens: int | None
    response_model: str | None
    memory_present: bool | None
    memory_item_count: int | None
    memory_position_bucket: str | None
    finished_at_unix_ms: int


@dataclass(frozen=True)
class _RestoredExactWitness:
    witness: ProviderRequestWitness
    input_tokens: int | None
    response_model: str | None


class PromptCacheTraceTracker:
    """Track the previous request for each provider/model/cache-key namespace."""

    def __init__(self) -> None:
        self._previous: dict[str, _PreviousRequest] = {}
        self._restored: dict[str, _RestoredLineage] = {}
        self._last_completed: _PreviousRequest | None = None
        self._last_user_completed: _PreviousRequest | None = None
        self._restored_cross_turn: _RestoredCrossTurn | None = None
        self._restored_exact_witness: _RestoredExactWitness | None = None

    def restore_exact_witness(
        self,
        witness: ProviderRequestWitness,
        *,
        input_tokens: int | None = None,
        response_model: str | None = None,
    ) -> None:
        """Restore a content-free final-wire anchor without treating it as history."""

        self._restored_exact_witness = _RestoredExactWitness(
            witness=witness,
            input_tokens=input_tokens,
            response_model=response_model,
        )

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
        lineage = value.get("cross_turn_lineage")
        if self._restored_cross_turn is not None or not isinstance(lineage, dict):
            return
        if lineage.get("version") != 1:
            return
        self._restored_cross_turn = _RestoredCrossTurn(
            system_fingerprint=_optional_text(lineage.get("system_fingerprint")),
            persona_fingerprint=_optional_text(lineage.get("persona_fingerprint")),
            tool_schema_fingerprint=_optional_text(lineage.get("tool_schema_fingerprint")),
            skill_fingerprint=_optional_text(lineage.get("skill_fingerprint")),
            runtime_fingerprint=_optional_text(lineage.get("runtime_fingerprint")),
            settings_fingerprint=_optional_text(lineage.get("settings_fingerprint")),
            model_route_fingerprint=_optional_text(lineage.get("model_route_fingerprint")),
            core_messages_fingerprint=_optional_text(lineage.get("core_messages_fingerprint")),
            core_message_count=_optional_nonnegative_int(lineage.get("core_message_count")),
            transcript_item_count=_optional_nonnegative_int(lineage.get("transcript_item_count")),
            input_tokens=_optional_nonnegative_int(lineage.get("input_tokens")),
            response_model=_optional_text(lineage.get("response_model")),
            memory_present=(
                lineage.get("memory_present")
                if isinstance(lineage.get("memory_present"), bool)
                else None
            ),
            memory_item_count=_optional_nonnegative_int(lineage.get("memory_item_count")),
            memory_position_bucket=_optional_text(lineage.get("memory_position_bucket")),
            finished_at_unix_ms=_optional_nonnegative_int(lineage.get("finished_at_unix_ms"))
            or finished_at_unix_ms,
        )

    def begin(
        self,
        *,
        messages: list[dict[str, Any]],
        message_domains: tuple[str, ...] | None = None,
        current_input_position: int | None = None,
        current_memory_positions: tuple[int, ...] | None = None,
        tools: list[dict[str, Any]] | None,
        provider_model: str,
        api_base: str | None,
        prompt_cache_key: str | None,
        persona_id: str | None = None,
        settings: Mapping[str, Any] | None = None,
        model_route: Mapping[str, Any] | None = None,
        skills: Mapping[str, Any] | None = None,
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
        if message_domains is None or len(message_domains) != len(messages):
            message_domains = tuple(
                "runtime"
                if _is_runtime_message(message)
                else "system"
                if str(message.get("role", "")) == "system"
                else "messages"
                for message in messages
            )
        role_counts = dict(Counter(message_roles))
        runtime_messages = [
            message
            for message, domain in zip(messages, message_domains, strict=True)
            if domain == "runtime"
        ]
        system_messages = [
            message
            for message, domain in zip(messages, message_domains, strict=True)
            if str(message.get("role", "")) == "system" and domain == "system"
        ]
        all_memory_positions = tuple(
            index for index, domain in enumerate(message_domains) if domain == "memory"
        )
        selected_memory_positions = (
            all_memory_positions
            if current_memory_positions is None
            else tuple(
                index
                for index in current_memory_positions
                if 0 <= index < len(messages) and message_domains[index] == "memory"
            )
        )
        memory_item_digests = tuple(
            _fingerprint("memory-item", messages[index]) for index in selected_memory_positions
        )
        memory_positions = (
            tuple(index - current_input_position for index in selected_memory_positions)
            if current_input_position is not None
            else selected_memory_positions
        )
        memory_position_bucket = (
            "none"
            if not selected_memory_positions
            else "after_current_user"
            if current_input_position is not None
            and all(index > current_input_position for index in selected_memory_positions)
            else "before_current_user"
            if current_input_position is not None
            else _position_bucket(selected_memory_positions, len(messages))
        )
        core_messages = [
            message
            for message in messages
            if str(message.get("role", "")) != "system" and not _is_runtime_message(message)
        ]
        core_message_digests = tuple(
            _fingerprint("core-message", message) for message in core_messages
        )
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
            previous_trace = previous.trace
            lineage_state = "continuous"
            cache_idle_ms = max(0, now_ms - previous.finished_at_unix_ms)
            previous_cache_state = previous.cache_state
            common = 0
            for old_digest, new_digest in zip(
                previous_trace.message_digests, message_digests, strict=False
            ):
                if old_digest != new_digest:
                    break
                common += 1
            tool_schema_preserved = (
                previous_trace.tool_schema_fingerprint == tool_schema_fingerprint
            )
            prefix_preserved = (
                common == len(previous_trace.message_digests) and tool_schema_preserved
            )
            common_chars = sum(message_chars[:common])
            common_tokens = int(common_chars * CHARS_TO_TOKENS)
            divergence_index = common if common < len(previous_trace.message_digests) else None
            divergence_role = (
                message_roles[common]
                if divergence_index is not None and common < len(message_roles)
                else previous_trace.message_roles[common]
                if divergence_index is not None and common < len(previous_trace.message_roles)
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
            skill_fingerprint=_fingerprint("skills", dict(skills or {})),
            persona_fingerprint=(
                _fingerprint("persona", persona_id) if persona_id is not None else None
            ),
            runtime_fingerprint=_fingerprint("runtime", runtime_messages),
            settings_fingerprint=_fingerprint("request-settings", dict(settings or {})),
            model_route_fingerprint=_fingerprint("model-route", dict(model_route or {})),
            message_digests=message_digests,
            core_message_digests=core_message_digests,
            message_chars=message_chars,
            message_roles=message_roles,
            message_domains=message_domains,
            memory_item_digests=memory_item_digests,
            memory_positions=memory_positions,
            memory_position_bucket=memory_position_bucket,
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

    def comparison_for(
        self,
        trace: PromptRequestTrace,
        *,
        run_id: str,
        user_turn: bool = True,
    ) -> PromptCacheComparison:
        """Bind the current request to the last completed user-run request."""

        recent = self._last_completed
        previous: _PreviousRequest | None
        if recent is not None and recent.run_id == run_id:
            previous = recent
            scope = "within_run"
        elif user_turn:
            previous = self._last_user_completed
            scope = "cross_user_turn"
        else:
            previous = None
            scope = "unavailable"
        if previous is not None and previous.run_id is not None:
            flags = _exact_change_flags(previous.trace, trace)
            precision = "exact_item" if previous.witness is not None else "domain_only"
            first_domain = _first_changed_domain(flags)
            coarse_relation = (
                None
                if previous.witness is not None
                else "coarse_domain_change"
                if _has_changed_domain(flags)
                else "unavailable_precision_reduced"
            )
            return PromptCacheComparison(
                scope=scope,
                precision=precision,
                previous_witness=previous.witness,
                previous_item_count=(
                    len(previous.witness.transcript_item_digests)
                    if previous.witness is not None
                    else None
                ),
                previous_input_tokens=previous.input_tokens,
                previous_response_model=previous.response_model,
                first_divergence_domain=first_domain,
                first_divergence_index=trace.first_divergence_index,
                first_divergence_role=trace.first_divergence_role,
                coarse_relation=coarse_relation,
                change_flags=flags,
            )

        restored_exact = self._restored_exact_witness if user_turn else None
        if restored_exact is not None:
            return PromptCacheComparison(
                scope="cross_process_resume",
                precision="exact_item",
                previous_witness=restored_exact.witness,
                previous_item_count=len(restored_exact.witness.transcript_item_digests),
                previous_input_tokens=restored_exact.input_tokens,
                previous_response_model=restored_exact.response_model,
            )

        restored = self._restored_cross_turn if user_turn else None
        if restored is not None:
            flags = _coarse_change_flags(restored, trace)
            return PromptCacheComparison(
                scope="cross_process_resume",
                precision="domain_only",
                previous_item_count=restored.transcript_item_count,
                previous_input_tokens=restored.input_tokens,
                previous_response_model=restored.response_model,
                first_divergence_domain=_first_changed_domain(flags),
                coarse_relation=(
                    "coarse_domain_change"
                    if _has_changed_domain(flags)
                    else "unavailable_precision_reduced"
                ),
                change_flags=flags,
            )

        return PromptCacheComparison(scope="unavailable", precision="unavailable")

    def finish(
        self,
        trace: PromptRequestTrace,
        *,
        cache_state: str,
        run_id: str | None = None,
        witness: ProviderRequestWitness | None = None,
        input_tokens: int | None = None,
        response_model: str | None = None,
        user_turn: bool = True,
    ) -> dict[str, Any]:
        """Advance lineage and return the durable, content-free summary."""
        finished_at_unix_ms = int(time.time() * 1000)
        completed = _PreviousRequest(
            trace=trace,
            run_id=run_id,
            witness=witness,
            input_tokens=input_tokens,
            response_model=response_model,
            finished_at_unix_ms=finished_at_unix_ms,
            cache_state=cache_state,
        )
        self._previous[trace.namespace] = completed
        if run_id is not None:
            self._last_completed = completed
        if run_id is not None and user_turn:
            self._last_user_completed = completed
            self._restored_cross_turn = None
            self._restored_exact_witness = None
        self._restored.pop(trace.namespace, None)
        durable = {
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
        if run_id is not None and user_turn:
            durable["cross_turn_lineage"] = {
                "version": 1,
                "system_fingerprint": trace.system_fingerprint,
                "persona_fingerprint": trace.persona_fingerprint,
                "tool_schema_fingerprint": trace.tool_schema_fingerprint,
                "skill_fingerprint": trace.skill_fingerprint,
                "runtime_fingerprint": trace.runtime_fingerprint,
                "settings_fingerprint": trace.settings_fingerprint,
                "model_route_fingerprint": trace.model_route_fingerprint,
                "core_messages_fingerprint": _fingerprint(
                    "core-message-sequence",
                    trace.core_message_digests,
                ),
                "core_message_count": len(trace.core_message_digests),
                "transcript_item_count": (
                    len(witness.transcript_item_digests) if witness is not None else None
                ),
                "role_counts": dict(trace.role_counts),
                "input_tokens": input_tokens,
                "response_model": response_model,
                "memory_present": bool(trace.memory_item_digests),
                "memory_item_count": len(trace.memory_item_digests),
                "memory_position_bucket": trace.memory_position_bucket,
                "finished_at_unix_ms": finished_at_unix_ms,
            }
        return durable
