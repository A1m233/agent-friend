from __future__ import annotations

from agent.tool_runtime import NoProgressTracker, ToolRunPolicy, ToolRunState
from agent.tools import ToolResult


def test_policy_dimensions_are_unlimited_by_default() -> None:
    policy = ToolRunPolicy()
    state = ToolRunState(policy)
    state.model_continuations = 100
    state.total_tool_calls = 1000
    assert state.hard_limit(continuation=True) is None
    assert state.hard_limit(additional_calls=100) is None


def test_budget_limit_and_soft_reminder() -> None:
    state = ToolRunState(
        ToolRunPolicy(max_model_continuations=10, max_total_tool_calls=5, soft_limit_ratio=0.8)
    )
    state.model_continuations = 8
    state.total_tool_calls = 4
    reminder = state.soft_reminder()
    assert reminder and "model_continuations" in reminder and "total_tool_calls" in reminder
    assert state.soft_reminder() is None
    assert state.hard_limit(additional_calls=2) == "total_tool_calls"
    state.model_continuations = 10
    assert state.hard_limit(continuation=True) == "model_continuations"


def test_no_progress_counts_only_repeated_errors_or_empty_results() -> None:
    tracker = NoProgressTracker(3)
    args = {"query": "same"}
    failed = ToolResult("network failed", is_error=True, meta={"error_kind": "network"})
    assert tracker.observe("web_search", args, failed) is False
    assert tracker.observe("web_search", args, failed) is False
    assert tracker.observe("web_search", args, failed) is True
    assert tracker.observe("web_search", args, ToolResult("useful result")) is False
