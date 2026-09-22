"""Local wait policy; never part of a model's JSON input or retry budget."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import httpx


class ModelCallDeadlineExceeded(TimeoutError):
    """A caller-owned deadline expired; retrying cannot extend it."""


@dataclass(frozen=True)
class ModelTimeoutPolicy:
    connect_seconds: float = 15.0
    write_seconds: float = 300.0
    response_seconds: float = 300.0
    idle_seconds: float = 300.0
    source: str = "interactive_default"

    def __post_init__(self) -> None:
        for value in (
            self.connect_seconds,
            self.write_seconds,
            self.response_seconds,
            self.idle_seconds,
        ):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("model timeout values must be finite and positive")

    def capped(self, seconds: float, source: str) -> ModelTimeoutPolicy:
        if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("model timeout must be finite and positive")
        return replace(
            self,
            connect_seconds=min(self.connect_seconds, seconds),
            write_seconds=min(self.write_seconds, seconds),
            response_seconds=min(self.response_seconds, seconds),
            idle_seconds=min(self.idle_seconds, seconds),
            source=source,
        )

    def http_timeout(self) -> httpx.Timeout:
        # read is per network chunk, not a total duration. The host separately
        # bounds SDK event silence and opening, including buffered responses.
        return httpx.Timeout(
            connect=self.connect_seconds,
            pool=self.connect_seconds,
            write=self.write_seconds,
            read=max(self.response_seconds, self.idle_seconds),
        )


def resolve_timeout_policy(
    defaults: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    streaming: bool,
    provider_timeout_is_default: bool = False,
    explicit: ModelTimeoutPolicy | None = None,
    legacy_stream_seconds: float | None = None,
) -> ModelTimeoutPolicy:
    if explicit is not None:
        return explicit
    policy = (
        ModelTimeoutPolicy()
        if streaming
        else ModelTimeoutPolicy(
            write_seconds=90, response_seconds=90, idle_seconds=90, source="completion_default"
        )
    )
    value = settings.get("timeout")
    source = "request_override"
    if value is None:
        value = None if provider_timeout_is_default else defaults.get("timeout")
        source = "provider_override"
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("legacy model timeout must be a positive number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("legacy model timeout must be finite and positive")
        policy = replace(
            policy,
            connect_seconds=min(policy.connect_seconds, value),
            write_seconds=value,
            response_seconds=value,
            idle_seconds=value,
            source=source,
        )
    if streaming and legacy_stream_seconds is not None:
        policy = policy.capped(legacy_stream_seconds, "legacy_stream_override")
    return policy


def controlled_cancel(
    check: Callable[[], None] | None,
    deadline: float | None,
) -> Callable[[], None] | None:
    if deadline is None:
        return check
    if deadline is not None and not math.isfinite(deadline):
        raise ValueError("deadline must be finite")

    def poll() -> None:
        if check is not None:
            check()
        if deadline is not None and time.monotonic() >= deadline:
            raise ModelCallDeadlineExceeded("model call business deadline exceeded")

    return poll
