"""Process-local cache epoch classification; never stores provider wire state."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class CacheEnvelopeState:
    persona_id: str | None
    system_prompt: str
    tools: str
    settings: str
    model_route: str


@dataclass(frozen=True)
class CacheEpochUpdate:
    generation: int
    changed: bool
    reason: str


@dataclass(frozen=True)
class PreparedCacheEpoch:
    envelope: CacheEnvelopeState
    update: CacheEpochUpdate
    epoch_id: str


@dataclass
class SessionCacheEpoch:
    generation: int = 0
    reason: str = "session_start"
    envelope: CacheEnvelopeState | None = None
    epoch_id: str = field(default_factory=lambda: str(uuid4()))
    recovery_disposition: str = "fresh"
    exact_continuation_candidate: bool = False

    def advance(self, reason: str) -> CacheEpochUpdate:
        self.generation += 1
        self.reason = reason
        self.epoch_id = str(uuid4())
        self.exact_continuation_candidate = False
        return CacheEpochUpdate(self.generation, True, reason)

    def observe(
        self,
        *,
        persona_id: str | None,
        system_prompt: str,
        tools: Sequence[Mapping[str, Any]] | None,
        settings: Mapping[str, Any],
        model_route: str,
        history_rewrite_reason: str | None = None,
    ) -> CacheEpochUpdate:
        prepared = self.prepare(
            persona_id=persona_id,
            system_prompt=system_prompt,
            tools=tools,
            settings=settings,
            model_route=model_route,
            history_rewrite_reason=history_rewrite_reason,
        )
        self.commit(prepared)
        return prepared.update

    def prepare(
        self,
        *,
        persona_id: str | None,
        system_prompt: str,
        tools: Sequence[Mapping[str, Any]] | None,
        settings: Mapping[str, Any],
        model_route: str,
        history_rewrite_reason: str | None = None,
    ) -> PreparedCacheEpoch:
        current = CacheEnvelopeState(
            persona_id=persona_id,
            system_prompt=system_prompt,
            tools=_canonical(tools or []),
            settings=_canonical(settings),
            model_route=model_route,
        )
        previous = self.envelope
        if previous is None:
            return PreparedCacheEpoch(
                current,
                CacheEpochUpdate(self.generation, False, self.reason),
                self.epoch_id,
            )
        reason = history_rewrite_reason or _envelope_change_reason(previous, current)
        if reason is None:
            return PreparedCacheEpoch(
                current,
                CacheEpochUpdate(self.generation, False, self.reason),
                self.epoch_id,
            )
        return PreparedCacheEpoch(
            current,
            CacheEpochUpdate(self.generation + 1, True, reason),
            str(uuid4()),
        )

    def commit(self, prepared: PreparedCacheEpoch) -> None:
        self.envelope = prepared.envelope
        self.generation = prepared.update.generation
        self.reason = prepared.update.reason
        self.epoch_id = prepared.epoch_id
        if prepared.update.changed:
            self.exact_continuation_candidate = False


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _envelope_change_reason(
    previous: CacheEnvelopeState,
    current: CacheEnvelopeState,
) -> str | None:
    if previous.model_route != current.model_route:
        return "model_route_changed"
    if previous.settings != current.settings:
        return "request_settings_changed"
    if previous.tools != current.tools:
        return "core_tools_changed"
    if previous.persona_id != current.persona_id:
        return "persona_changed"
    if previous.system_prompt != current.system_prompt:
        return "system_prompt_changed"
    return None


__all__ = [
    "CacheEnvelopeState",
    "CacheEpochUpdate",
    "PreparedCacheEpoch",
    "SessionCacheEpoch",
]
