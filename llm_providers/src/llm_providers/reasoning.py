"""Provider-private reasoning continuation capture and replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .catalog import ModelRuntimeConfig, ReasoningReplayScope
from .model_identity import is_custom_model_reference
from .spec import ProviderSpec

ReasoningStateFormat = Literal["reasoning_content", "reasoning_details"]


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(exclude_none=True)
        except Exception:
            return None
        if isinstance(dumped, Mapping):
            return {str(key): item for key, item in dumped.items()}
    return None


def reasoning_detail_objects(value: Any) -> tuple[dict[str, Any], ...]:
    """Return JSON-like reasoning detail objects without interpreting fields."""

    if value is None:
        return ()
    values: Sequence[Any]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = value
    else:
        values = (value,)
    return tuple(item for raw in values if (item := _json_object(raw)) is not None)


@dataclass(frozen=True, repr=False)
class ProviderReasoningState:
    """Opaque provider payload that may be replayed in model-visible history."""

    provider_id: str
    model_id: str
    format: ReasoningStateFormat
    replay_scope: ReasoningReplayScope
    payload: str | tuple[Mapping[str, Any], ...]

    def __repr__(self) -> str:
        size = len(self.payload)
        return (
            "ProviderReasoningState("
            f"provider_id={self.provider_id!r}, model_id={self.model_id!r}, "
            f"format={self.format!r}, replay_scope={self.replay_scope!r}, size={size})"
        )

    def to_event_payload(self) -> dict[str, Any]:
        data: str | list[dict[str, Any]]
        if self.format == "reasoning_content":
            if not isinstance(self.payload, str):
                raise ValueError("reasoning_content payload 必须是字符串")
            data = self.payload
        else:
            if isinstance(self.payload, str):
                raise ValueError("reasoning_details payload 必须是对象序列")
            data = [dict(item) for item in self.payload]
        return {
            "provider": self.provider_id,
            "model": self.model_id,
            "format": self.format,
            "replayScope": self.replay_scope,
            "data": data,
        }

    @classmethod
    def from_event_payload(cls, payload: Mapping[str, Any]) -> ProviderReasoningState:
        provider_id = payload.get("provider")
        model_id = payload.get("model")
        state_format = payload.get("format")
        replay_scope = payload.get("replayScope")
        data = payload.get("data")
        if not isinstance(provider_id, str) or not isinstance(model_id, str):
            raise ValueError("reasoning state provider/model 无效")
        if state_format not in {"reasoning_content", "reasoning_details"}:
            raise ValueError("reasoning state format 无效")
        if replay_scope not in {"none", "tool_turn", "conversation"}:
            raise ValueError("reasoning state replayScope 无效")
        if state_format == "reasoning_content":
            if not isinstance(data, str):
                raise ValueError("reasoning_content data 无效")
            normalized: str | tuple[Mapping[str, Any], ...] = data
        else:
            details = reasoning_detail_objects(data)
            if not details or not isinstance(data, list) or len(details) != len(data):
                raise ValueError("reasoning_details data 无效")
            normalized = details
        return cls(
            provider_id=provider_id,
            model_id=model_id,
            format=state_format,
            replay_scope=replay_scope,
            payload=normalized,
        )


def build_provider_reasoning_state(
    spec: ProviderSpec,
    *,
    reasoning_content: str,
    reasoning_details: Sequence[Mapping[str, Any]],
) -> ProviderReasoningState | None:
    """Build an opaque state only when the bound catalog requires replay."""

    if (
        spec.reasoning_replay_scope == "none"
        or spec.provider_id is None
        or spec.product_model_id is None
    ):
        return None
    scope: ReasoningReplayScope = spec.reasoning_replay_scope  # type: ignore[assignment]
    if reasoning_details:
        return ProviderReasoningState(
            provider_id=spec.provider_id,
            model_id=spec.product_model_id,
            format="reasoning_details",
            replay_scope=scope,
            payload=tuple(dict(item) for item in reasoning_details),
        )
    if reasoning_content:
        return ProviderReasoningState(
            provider_id=spec.provider_id,
            model_id=spec.product_model_id,
            format="reasoning_content",
            replay_scope=scope,
            payload=reasoning_content,
        )
    return None


def render_provider_reasoning_state(
    message: Mapping[str, Any],
    state: ProviderReasoningState,
    *,
    config: ModelRuntimeConfig,
    state_run_id: str | None,
    active_run_id: str | None,
) -> dict[str, Any]:
    """Attach a compatible state to one already-rendered assistant message."""

    rendered = dict(message)
    if rendered.get("role") != "assistant":
        return rendered
    if (
        config.reasoning_preset in {"off", "none"}
        and not is_custom_model_reference(config.model_id)
    ) or state.model_id != config.model_id:
        return rendered
    if state.replay_scope == "tool_turn" and (
        not state_run_id or not active_run_id or state_run_id != active_run_id
    ):
        return rendered
    if state.format == "reasoning_content":
        if isinstance(state.payload, str):
            rendered["reasoning_content"] = state.payload
    elif not isinstance(state.payload, str):
        rendered["reasoning_details"] = [dict(item) for item in state.payload]
    return rendered


__all__ = [
    "ProviderReasoningState",
    "ReasoningStateFormat",
    "build_provider_reasoning_state",
    "reasoning_detail_objects",
    "render_provider_reasoning_state",
]
