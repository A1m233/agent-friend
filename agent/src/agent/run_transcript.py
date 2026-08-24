"""Run-local provider transcript continuation projection.

This module deliberately does not persist state.  Session events and
ContextManager remain authoritative; the projection only keeps the previous
prepared request long enough to preserve its provider-visible prefix during one
active run.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal

from llm_providers import ProviderAssistantTurn, ProviderRequestWitness

from .context import CHARS_TO_TOKENS

ContinuationDisposition = Literal["baseline", "continued", "rebuilt"]


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
    return sha256(f"agent-friend:{label}:v1\0".encode() + payload).hexdigest()


@dataclass(frozen=True)
class ContinuationBuild:
    messages: list[dict[str, Any]]
    disposition: ContinuationDisposition
    generation: int
    rebase_reason: str | None


@dataclass
class RunContinuationProjection:
    """Previous prepared request plus a completed response, scoped to one run."""

    continuation_id: str
    generation: int = 0
    previous_messages: list[dict[str, Any]] | None = None
    previous_witness: ProviderRequestWitness | None = None
    envelope_fingerprint: str | None = None
    tool_fingerprint: str | None = None
    settings_fingerprint: str | None = None
    model_identity: str | None = None
    active_event_uuids: tuple[str, ...] = ()
    event_cursor: int = 0
    pending_assistant: ProviderAssistantTurn | None = None
    current_disposition: ContinuationDisposition = "baseline"
    current_rebase_reason: str | None = None
    _observed_witness: ProviderRequestWitness | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False)

    def prepare(
        self,
        *,
        canonical_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        settings: dict[str, Any],
        model_identity: str,
        active_event_uuids: tuple[str, ...],
        event_cursor: int,
        tool_messages: list[dict[str, Any]] | None = None,
        host_tail: list[dict[str, Any]] | None = None,
        context_notes: dict[str, Any] | None = None,
        forced_rebase_reason: str | None = None,
    ) -> ContinuationBuild:
        if self._closed:
            raise RuntimeError("run continuation projection is closed")
        tool_fingerprint = _fingerprint("run-tools", tools or [])
        settings_fingerprint = _fingerprint("run-settings", settings)
        envelope = _fingerprint(
            "run-request-envelope",
            {
                "model_identity": model_identity,
                "tools": tools or [],
                "settings": settings,
            },
        )
        reason = forced_rebase_reason or self._continuation_rejection_reason(
            model_identity=model_identity,
            tool_fingerprint=tool_fingerprint,
            settings_fingerprint=settings_fingerprint,
            active_event_uuids=active_event_uuids,
            context_notes=context_notes or {},
            tool_messages=tool_messages,
        )
        if reason is None:
            assert self.previous_messages is not None
            assert self.pending_assistant is not None
            candidate = [
                *copy.deepcopy(self.previous_messages),
                self.pending_assistant.to_openai_message(),
                *copy.deepcopy(tool_messages or []),
                *copy.deepcopy(host_tail or []),
            ]
            reason = self._candidate_budget_reason(candidate, context_notes or {})
            if reason is None:
                self.previous_messages = candidate
                self.envelope_fingerprint = envelope
                self.tool_fingerprint = tool_fingerprint
                self.settings_fingerprint = settings_fingerprint
                self.model_identity = model_identity
                self.active_event_uuids = active_event_uuids
                self.event_cursor = event_cursor
                self.pending_assistant = None
                self.current_disposition = "continued"
                self.current_rebase_reason = None
                return ContinuationBuild(candidate, "continued", self.generation, None)

        disposition: ContinuationDisposition
        if self.previous_messages is None:
            disposition = "baseline"
            reason = "run_start"
        else:
            disposition = "rebuilt"
            self.generation += 1
        self.previous_messages = copy.deepcopy(canonical_messages)
        self.envelope_fingerprint = envelope
        self.tool_fingerprint = tool_fingerprint
        self.settings_fingerprint = settings_fingerprint
        self.model_identity = model_identity
        self.active_event_uuids = active_event_uuids
        self.event_cursor = event_cursor
        self.pending_assistant = None
        self.current_disposition = disposition
        self.current_rebase_reason = reason
        return ContinuationBuild(
            copy.deepcopy(canonical_messages),
            disposition,
            self.generation,
            reason,
        )

    def _continuation_rejection_reason(
        self,
        *,
        model_identity: str,
        tool_fingerprint: str,
        settings_fingerprint: str,
        active_event_uuids: tuple[str, ...],
        context_notes: dict[str, Any],
        tool_messages: list[dict[str, Any]] | None,
    ) -> str | None:
        if self.previous_messages is None:
            return "run_start"
        if self.pending_assistant is None:
            return "provider_response_unreplayable"
        if active_event_uuids[: len(self.active_event_uuids)] != self.active_event_uuids:
            return "history_rewritten"
        if self.model_identity != model_identity:
            return "model_config_changed"
        if self.tool_fingerprint != tool_fingerprint:
            return "tool_envelope_changed"
        if self.settings_fingerprint != settings_fingerprint:
            return "request_settings_changed"
        if not tool_messages and self.pending_assistant.tool_calls:
            return "tool_transaction_incomplete"
        if context_notes.get("compacted") or context_notes.get("new_compaction"):
            return "context_compacted"
        if context_notes.get("checkpoint_status") == "candidate":
            return "context_compacted"
        if int(context_notes.get("dropped_count") or 0) > 0:
            return "history_truncated"
        if int(context_notes.get("tool_results_truncated") or 0) > 0:
            return "tail_tool_projection"
        if int(context_notes.get("tool_results_omitted") or 0) > 0:
            return "tail_tool_projection"
        if context_notes.get("tool_context_checkpoint_status") == "candidate":
            return "tool_context_rewritten"
        return None

    @staticmethod
    def _candidate_budget_reason(
        candidate: list[dict[str, Any]],
        context_notes: dict[str, Any],
    ) -> str | None:
        threshold = context_notes.get("trigger_threshold")
        if not isinstance(threshold, int) or threshold <= 0:
            return None
        calibration = context_notes.get("calibration_factor", 1.0)
        factor = float(calibration) if isinstance(calibration, (int, float)) else 1.0
        overhead = context_notes.get("request_overhead_tokens", 0)
        overhead_tokens = int(overhead) if isinstance(overhead, (int, float)) else 0
        estimate = int(len(_canonical_json(candidate)) * CHARS_TO_TOKENS * max(1.0, factor))
        if estimate + overhead_tokens > threshold:
            return "continuation_over_budget"
        return None

    def record_completed_turn(
        self,
        assistant_turn: ProviderAssistantTurn,
        witness: ProviderRequestWitness | None,
    ) -> None:
        if self._closed:
            return
        self.pending_assistant = assistant_turn
        self.observe_request_witness(witness)

    def observe_request_witness(self, witness: ProviderRequestWitness | None) -> None:
        """Reconcile the agent-side continuation decision with the final wire body."""
        if self._closed:
            return
        if (
            witness is not None
            and self._observed_witness is not None
            and witness.request_digest == self._observed_witness.request_digest
        ):
            return
        if witness is None:
            if self.current_disposition == "continued":
                self.current_rebase_reason = "wire_witness_unavailable"
        elif self.current_disposition == "continued":
            relation = witness.relation_to(self.previous_witness)
            if relation != "strict_extension":
                self.generation += 1
                self.current_disposition = "rebuilt"
                self.current_rebase_reason = "provider_transform_changed"
        self.previous_witness = witness
        self._observed_witness = witness

    def replace_prepared(self, messages: list[dict[str, Any]], *, reason: str) -> None:
        """Adopt an explicit recovery rebuild as the generation's new baseline."""
        if self._closed:
            return
        self.generation += 1
        self.previous_messages = copy.deepcopy(messages)
        self.pending_assistant = None
        self.current_disposition = "rebuilt"
        self.current_rebase_reason = reason

    def close(self) -> None:
        self._closed = True
        self.previous_messages = None
        self.previous_witness = None
        self._observed_witness = None
        self.envelope_fingerprint = None
        self.tool_fingerprint = None
        self.settings_fingerprint = None
        self.model_identity = None
        self.active_event_uuids = ()
        self.pending_assistant = None


__all__ = ["ContinuationBuild", "ContinuationDisposition", "RunContinuationProjection"]
