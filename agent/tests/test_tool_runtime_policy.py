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


def test_no_progress_groups_same_error_across_changed_arguments() -> None:
    tracker = NoProgressTracker(3)
    blocked = ToolResult(
        "sandbox setup required",
        is_error=True,
        meta={
            "error_kind": "windows_sandbox_setup_required",
            "no_progress_family": "windows_sandbox_setup_required",
        },
    )
    assert tracker.observe("exec_command", {"cmd": "git clone"}, blocked) is False
    assert tracker.observe("exec_command", {"cmd": "pwsh download"}, blocked) is False
    assert tracker.observe("exec_command", {"cmd": "python download"}, blocked) is True


def test_no_progress_does_not_group_unspecified_errors_across_changed_arguments() -> None:
    tracker = NoProgressTracker(3)
    failed = ToolResult("exit 1", is_error=True, meta={"error_kind": None})

    assert tracker.observe("exec_command", {"cmd": "maa --help"}, failed) is False
    assert tracker.observe("exec_command", {"cmd": "maa version"}, failed) is False
    assert tracker.observe("exec_command", {"cmd": "maa dir"}, failed) is False


def test_no_progress_still_stops_an_identical_failing_command() -> None:
    tracker = NoProgressTracker(3)
    failed = ToolResult("exit 1", is_error=True)
    args = {"cmd": "maa --help"}

    assert tracker.observe("exec_command", args, failed) is False
    assert tracker.observe("exec_command", args, failed) is False
    assert tracker.observe("exec_command", args, failed) is True


def test_success_resets_cross_argument_error_family() -> None:
    tracker = NoProgressTracker(2)
    failed = ToolResult("failed", is_error=True, meta={"error_kind": "network"})
    assert tracker.observe("web_fetch", {"url": "a"}, failed) is False
    assert tracker.observe("web_fetch", {"url": "ok"}, ToolResult("page")) is False
    assert tracker.observe("web_fetch", {"url": "b"}, failed) is False


def test_no_progress_uses_evidence_delta_and_keeps_intents_independent() -> None:
    tracker = NoProgressTracker(2)
    first = ToolResult("page", meta={"evidence_keys": ["https://example.com/a"]})
    repeated = ToolResult("same page", meta={"evidence_keys": ["https://example.com/a"]})

    assert tracker.observe("web_fetch", {"evidence_ref": "a"}, first) is False
    assert tracker.observe("web_fetch", {"evidence_ref": "a"}, repeated) is False
    assert tracker.observe("web_fetch", {"evidence_ref": "b"}, repeated) is False
    assert tracker.observe("web_fetch", {"evidence_ref": "a"}, repeated) is True
    assert tracker.new_evidence_count == 1
    assert tracker.reused_evidence_count == 3
    assert tracker.last_intent_signature is not None


def test_new_evidence_resets_no_progress_for_same_intent() -> None:
    tracker = NoProgressTracker(2)
    args = {"query": "creator"}
    first = ToolResult("first", meta={"evidence_keys": ["creator:1"]})
    repeated = ToolResult("repeat", meta={"evidence_keys": ["creator:1"]})
    expanded = ToolResult("expanded", meta={"evidence_keys": ["creator:1", "video:2"]})

    assert tracker.observe("social_search", args, first) is False
    assert tracker.observe("social_search", args, repeated) is False
    assert tracker.observe("social_search", args, expanded) is False
    assert tracker.observe("social_search", args, repeated) is False
