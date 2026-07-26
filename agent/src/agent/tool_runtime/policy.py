"""Configurable per-turn budgets and no-progress detection."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..tools.protocol import ToolResult


@dataclass(frozen=True)
class ToolRunPolicy:
    """Tool-loop limits. ``None`` means that dimension is intentionally unlimited."""

    max_model_continuations: int | None = None
    max_total_tool_calls: int | None = None
    max_elapsed_seconds: float | None = None
    soft_limit_ratio: float = 0.8
    default_tool_timeout_seconds: float = 60.0
    max_parallel_read_tools: int = 4
    repeated_no_progress_limit: int = 3

    def __post_init__(self) -> None:
        for name in ("max_model_continuations", "max_total_tool_calls"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive or None")
        if self.max_elapsed_seconds is not None and self.max_elapsed_seconds <= 0:
            raise ValueError("max_elapsed_seconds must be positive or None")
        if not 0 < self.soft_limit_ratio < 1:
            raise ValueError("soft_limit_ratio must be between 0 and 1")
        if self.default_tool_timeout_seconds <= 0:
            raise ValueError("default_tool_timeout_seconds must be positive")
        if self.max_parallel_read_tools < 1:
            raise ValueError("max_parallel_read_tools must be at least 1")
        if self.repeated_no_progress_limit < 2:
            raise ValueError("repeated_no_progress_limit must be at least 2")


@dataclass
class ToolRunState:
    policy: ToolRunPolicy
    started_at: float = field(default_factory=time.monotonic)
    model_continuations: int = 0
    total_tool_calls: int = 0
    soft_notified_dimensions: set[str] = field(default_factory=set)
    stop_reason: str | None = None
    stop_dimension: str | None = None
    timeout_count: int = 0
    blocked_count: int = 0
    parallel_batch_count: int = 0

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def hard_limit(self, *, additional_calls: int = 0, continuation: bool = False) -> str | None:
        if (
            self.policy.max_elapsed_seconds is not None
            and self.elapsed_seconds >= self.policy.max_elapsed_seconds
        ):
            return "elapsed"
        if (
            continuation
            and self.policy.max_model_continuations is not None
            and self.model_continuations >= self.policy.max_model_continuations
        ):
            return "model_continuations"
        if (
            self.policy.max_total_tool_calls is not None
            and self.total_tool_calls + additional_calls > self.policy.max_total_tool_calls
        ):
            return "total_tool_calls"
        return None

    def remaining_tool_calls(self) -> int | None:
        limit = self.policy.max_total_tool_calls
        return None if limit is None else max(0, limit - self.total_tool_calls)

    def soft_reminder(self) -> str | None:
        fragments: list[str] = []
        candidates = (
            ("model_continuations", self.model_continuations, self.policy.max_model_continuations),
            ("total_tool_calls", self.total_tool_calls, self.policy.max_total_tool_calls),
            ("elapsed", self.elapsed_seconds, self.policy.max_elapsed_seconds),
        )
        for name, used, limit in candidates:
            if limit is None or name in self.soft_notified_dimensions:
                continue
            if used / limit >= self.policy.soft_limit_ratio:
                self.soft_notified_dimensions.add(name)
                remaining = max(0, limit - used)
                fragments.append(
                    f"{name} remaining={remaining:.1f}"
                    if isinstance(remaining, float)
                    else f"{name} remaining={remaining}"
                )
        if not fragments:
            return None
        return (
            "工具运行预算即将到达边界："
            + "; ".join(fragments)
            + "。请停止非必要扩张，优先完成关键取证并基于已有证据收敛回答。"
        )

    def mark_stopped(self, reason: str, dimension: str | None = None) -> None:
        self.stop_reason = reason
        self.stop_dimension = dimension

    def diagnostics(self) -> dict[str, Any]:
        return {
            "stop_reason": self.stop_reason or "end_turn",
            "budget_dimension": self.stop_dimension,
            "model_continuations": self.model_continuations,
            "total_tool_calls": self.total_tool_calls,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "timeout_count": self.timeout_count,
            "blocked_count": self.blocked_count,
            "parallel_batch_count": self.parallel_batch_count,
        }


class NoProgressTracker:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._last_signature: str | None = None
        self._count = 0

    def observe(self, name: str, args: dict[str, Any], result: ToolResult) -> bool:
        error_kind = str(result.meta.get("error_kind", ""))
        empty = not result.text.strip() or error_kind in {"empty_content", "no_results"}
        if not result.is_error and not empty:
            self._last_signature = None
            self._count = 0
            return False
        normalized_text = " ".join(result.text.lower().split())[:256]
        public_args = {key: value for key, value in args.items() if not key.startswith("__")}
        payload = json.dumps(
            [name, public_args, error_kind, normalized_text],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        signature = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if signature == self._last_signature:
            self._count += 1
        else:
            self._last_signature = signature
            self._count = 1
        return self._count >= self._limit
