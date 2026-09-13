"""Configurable per-turn budgets and advisory-only progress observations."""

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
    no_progress_advisory_threshold: int = 3

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
        if self.no_progress_advisory_threshold < 2:
            raise ValueError("no_progress_advisory_threshold must be at least 2")

    @property
    def repeated_no_progress_limit(self) -> int:
        """Compatibility view for callers migrating to advisory-only semantics."""

        return self.no_progress_advisory_threshold


@dataclass
class ToolRunState:
    policy: ToolRunPolicy
    started_at: float = field(default_factory=time.monotonic)
    model_continuations: int = 0
    total_tool_calls: int = 0
    tool_call_items: int = 0
    model_calls: int = 0
    soft_notified_dimensions: set[str] = field(default_factory=set)
    stop_reason: str | None = None
    stop_dimension: str | None = None
    timeout_count: int = 0
    blocked_count: int = 0
    parallel_batch_count: int = 0
    new_evidence_count: int = 0
    reused_evidence_count: int = 0
    last_intent_signature: str | None = None

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
            "tool_call_items": self.tool_call_items,
            "model_calls": self.model_calls,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "timeout_count": self.timeout_count,
            "blocked_count": self.blocked_count,
            "parallel_batch_count": self.parallel_batch_count,
            "new_evidence_count": self.new_evidence_count,
            "reused_evidence_count": self.reused_evidence_count,
            "last_intent_signature": self.last_intent_signature,
        }


@dataclass(frozen=True)
class ToolProgressObservation:
    intent_signature: str
    error_family: str
    same_intent_count: int
    same_error_family_count: int
    new_evidence_count: int
    reused_evidence_count: int
    advisory_due: bool


class ToolProgressTracker:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._counts: dict[str, int] = {}
        self._error_family_counts: dict[tuple[str, str], int] = {}
        self._advised_keys: set[str] = set()
        self._pending_advisory = False
        self._seen_evidence: set[str] = set()
        self.new_evidence_count = 0
        self.reused_evidence_count = 0
        self.last_intent_signature: str | None = None

    def observe(
        self,
        name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolProgressObservation:
        public_args = {key: value for key, value in args.items() if not key.startswith("__")}
        intent_payload = json.dumps(
            [name, public_args],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        signature = hashlib.sha256(intent_payload.encode("utf-8")).hexdigest()
        self.last_intent_signature = signature

        raw_keys = result.meta.get("evidence_keys")
        evidence_keys = (
            {value[:4096] for value in raw_keys[:100] if isinstance(value, str) and value}
            if isinstance(raw_keys, list)
            else set()
        )
        delta = evidence_keys - self._seen_evidence
        reused = evidence_keys & self._seen_evidence
        self.new_evidence_count += len(delta)
        self.reused_evidence_count += len(reused)
        if delta:
            self._seen_evidence.update(delta)
            self._counts[signature] = 0
            return self._observation(name, signature, "", False)

        raw_error_kind = result.meta.get("error_kind")
        error_kind = raw_error_kind if isinstance(raw_error_kind, str) else ""
        raw_no_progress_family = result.meta.get("no_progress_family")
        no_progress_family = (
            raw_no_progress_family if isinstance(raw_no_progress_family, str) else ""
        )
        if result.is_error and no_progress_family:
            error_family = (name, no_progress_family)
            self._error_family_counts[error_family] = (
                self._error_family_counts.get(error_family, 0) + 1
            )
        elif not result.is_error:
            self._error_family_counts = {
                key: count for key, count in self._error_family_counts.items() if key[0] != name
            }
        empty = (
            (not result.text.strip() and not result.images)
            or error_kind in {"empty_content", "no_results"}
            or result.meta.get("result_count") == 0
        )
        if not result.is_error and not empty and not evidence_keys:
            self._counts[signature] = 0
            return self._observation(name, signature, no_progress_family, False)
        self._counts[signature] = self._counts.get(signature, 0) + 1
        same_error_family_count = self._error_family_counts.get(
            (name, no_progress_family),
            0,
        )
        advisory_key = (
            f"error:{name}:{no_progress_family}"
            if no_progress_family and same_error_family_count >= self._limit
            else f"intent:{signature}"
            if self._counts[signature] >= self._limit
            else ""
        )
        advisory_due = bool(advisory_key and advisory_key not in self._advised_keys)
        if advisory_due:
            self._advised_keys.add(advisory_key)
            self._pending_advisory = True
        return self._observation(name, signature, no_progress_family, advisory_due)

    def take_advisory(self) -> str | None:
        """Return one pending model reminder; never authorizes a host stop."""

        if not self._pending_advisory:
            return None
        self._pending_advisory = False
        return (
            "最近的工具路线重复返回相同错误、空结果或已有证据。"
            "这只是进展提示，不是运行终止条件。请检查当前 schema 和错误结果，"
            "修正参数、改用其它安全工具、改变取证路线，或基于已有证据诚实回答；"
            "不要原样重复已经失败的调用。"
        )

    def _observation(
        self,
        name: str,
        signature: str,
        error_family: str,
        advisory_due: bool,
    ) -> ToolProgressObservation:
        same_error_family_count = max(
            (
                count
                for (tool_name, family), count in self._error_family_counts.items()
                if tool_name == name and family == error_family
            ),
            default=0,
        )
        return ToolProgressObservation(
            intent_signature=signature,
            error_family=error_family,
            same_intent_count=self._counts.get(signature, 0),
            same_error_family_count=same_error_family_count,
            new_evidence_count=self.new_evidence_count,
            reused_evidence_count=self.reused_evidence_count,
            advisory_due=advisory_due,
        )


# Compatibility alias for callers importing the pre-095 name.  The object is now
# advisory-only; its observations never stop a run.
NoProgressTracker = ToolProgressTracker
