"""Content-free lineage for comparable background model operations."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from typing import Any

from .protocol import ModelResponse
from .request_witness import ProviderRequestWitness

_LINEAGE_VERSION = 1


def _lineage_key(
    *,
    session_id: str,
    operation: str,
    call_kind: str,
    prompt_contract_revision: str,
) -> str:
    payload = "\0".join((session_id, operation, call_kind, prompt_contract_revision)).encode(
        "utf-8"
    )
    return sha256(
        f"agent-friend:operation-request-lineage:v{_LINEAGE_VERSION}\0".encode() + payload
    ).hexdigest()


@dataclass(frozen=True, repr=False)
class OperationLineageBinding:
    """One begin/complete token and the comparison visible to its request."""

    key: str | None
    generation: int
    scope: str
    precision: str
    previous_witness: ProviderRequestWitness | None = None
    previous_item_count: int | None = None
    previous_input_tokens: int | None = None
    previous_response_model: str | None = None
    unavailable_reason: str | None = None

    def comparison_fields(self) -> dict[str, Any]:
        return {
            "previous_item_count": self.previous_item_count,
            "previous_input_tokens": self.previous_input_tokens,
            "previous_response_model": self.previous_response_model,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class _OperationAnchor:
    witness: ProviderRequestWitness
    input_tokens: int | None
    response_model: str | None
    completed_at_monotonic: float


class OperationRequestLineageTracker:
    """Keep exact in-process anchors isolated by logical operation contract."""

    def __init__(self, *, max_entries: int = 256) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._lock = Lock()
        self._generation = 0
        self._latest_started: OrderedDict[str, int] = OrderedDict()
        self._anchors: OrderedDict[str, _OperationAnchor] = OrderedDict()

    def begin(
        self,
        *,
        session_id: str | None,
        operation: str,
        call_kind: str,
        prompt_contract_revision: str,
    ) -> OperationLineageBinding:
        if not session_id:
            return OperationLineageBinding(
                key=None,
                generation=0,
                scope="unavailable",
                precision="unavailable",
                unavailable_reason="missing_session_identity",
            )
        key = _lineage_key(
            session_id=session_id,
            operation=operation,
            call_kind=call_kind,
            prompt_contract_revision=prompt_contract_revision,
        )
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._latest_started[key] = generation
            self._latest_started.move_to_end(key)
            anchor = self._anchors.get(key)
            if anchor is not None:
                self._anchors.move_to_end(key)
            self._trim_locked()
        if anchor is None:
            return OperationLineageBinding(
                key=key,
                generation=generation,
                scope="unavailable",
                precision="unavailable",
                unavailable_reason="no_previous_operation_request",
            )
        return OperationLineageBinding(
            key=key,
            generation=generation,
            scope="within_operation",
            precision="exact_item",
            previous_witness=anchor.witness,
            previous_item_count=len(anchor.witness.transcript_item_digests),
            previous_input_tokens=anchor.input_tokens,
            previous_response_model=anchor.response_model,
        )

    def complete(self, binding: OperationLineageBinding, response: ModelResponse) -> bool:
        if binding.key is None:
            return False
        diagnostics = response.diagnostics
        witness = diagnostics.request_witness if diagnostics is not None else None
        if witness is None:
            return False
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage is not None else None
        response_model = diagnostics.response_model if diagnostics is not None else None
        with self._lock:
            if self._latest_started.get(binding.key) != binding.generation:
                return False
            self._anchors[binding.key] = _OperationAnchor(
                witness=witness,
                input_tokens=input_tokens,
                response_model=response_model,
                completed_at_monotonic=time.monotonic(),
            )
            self._anchors.move_to_end(binding.key)
            self._trim_locked()
        return True

    def _trim_locked(self) -> None:
        while len(self._latest_started) > self._max_entries:
            key, _ = self._latest_started.popitem(last=False)
            self._anchors.pop(key, None)
        while len(self._anchors) > self._max_entries:
            key, _ = self._anchors.popitem(last=False)
            self._latest_started.pop(key, None)


__all__ = ["OperationLineageBinding", "OperationRequestLineageTracker"]
