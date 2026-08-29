from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from llm_providers import (
    PROMPT_CACHE_OBSERVATION_LOG_PREFIX,
    PROMPT_CACHE_OBSERVATION_SCHEMA,
    PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION,
)


def _load_module() -> ModuleType:
    path = Path(__file__).parents[1] / "prompt-cache-report" / "run.py"
    spec = importlib.util.spec_from_file_location("agent_friend_prompt_cache_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _observation(
    *,
    input_total: int | None,
    cache_read: int | None,
    cache_write: int | None,
    input_other: int | None,
    operation: str,
    relation: str,
    comparison_scope: str = "unavailable",
    cross_turn_relation: str = "unavailable",
    first_divergence_domain: str | None = None,
    lost_prefix_tokens_estimate: int | None = None,
    messages_appended: bool | None = None,
    messages_rewritten: bool | None = None,
    unavailable_reason: str | None = None,
    memory_presence_changed: bool | None = None,
    memory_content_changed: bool | None = None,
    memory_position_changed: bool | None = None,
) -> dict[str, Any]:
    return {
        "schema": PROMPT_CACHE_OBSERVATION_SCHEMA,
        "schema_revision": PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION,
        "identity": {"operation": operation, "call_index": 1},
        "model": {"provider": "deepseek", "requested": "model", "response": "model"},
        "usage": {
            "input_total": input_total,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "input_other": input_other,
            "output": 5,
            "accounting": "inclusive_total",
            "buckets_reconciled": all(
                value is not None for value in (input_total, cache_read, cache_write, input_other)
            ),
        },
        "structure": {
            "final_wire_relation": relation,
            "comparison_scope": comparison_scope,
            "cross_turn_relation": cross_turn_relation,
            "first_divergence_domain": first_divergence_domain,
            "unavailable_reason": unavailable_reason,
            "lost_prefix_tokens_estimate": lost_prefix_tokens_estimate,
            "change_flags": {
                "messages_appended": messages_appended,
                "messages_rewritten": messages_rewritten,
                "memory_presence_changed": memory_presence_changed,
                "memory_content_changed": memory_content_changed,
                "memory_position_changed": memory_position_changed,
            },
            "rebase_reason": None,
        },
        "timing": {"stream_open_ms": 10, "first_chunk_ms": 20, "total_ms": 30},
        "outcome": {
            "status": "completed",
            "interpretation": "test",
            "response_cost": None,
        },
    }


def test_load_and_aggregate_preserves_denominators_and_unknowns(tmp_path: Path) -> None:
    module = _load_module()
    observations = [
        _observation(
            input_total=100,
            cache_read=50,
            cache_write=10,
            input_other=40,
            operation="conversation",
            relation="strict_extension",
            comparison_scope="cross_user_turn",
            cross_turn_relation="strict_extension",
            lost_prefix_tokens_estimate=0,
            messages_appended=True,
            messages_rewritten=False,
        ),
        _observation(
            input_total=300,
            cache_read=0,
            cache_write=None,
            input_other=None,
            operation="memory_extraction",
            relation="rebase",
            comparison_scope="cross_user_turn",
            cross_turn_relation="rebase",
            first_divergence_domain="settings",
            lost_prefix_tokens_estimate=120,
            messages_appended=True,
            messages_rewritten=False,
        ),
        _observation(
            input_total=None,
            cache_read=None,
            cache_write=None,
            input_other=None,
            operation="conversation",
            relation="unavailable",
        ),
    ]
    unsupported = dict(observations[0], schema_revision=999)
    log_path = tmp_path / "agent_bridge.log"
    lines = [
        "ordinary line",
        *[
            f"2026-08-29 INFO {PROMPT_CACHE_OBSERVATION_LOG_PREFIX}"
            + json.dumps(item, separators=(",", ":"))
            for item in observations
        ],
        f"INFO {PROMPT_CACHE_OBSERVATION_LOG_PREFIX}{{not-json",
        f"INFO {PROMPT_CACHE_OBSERVATION_LOG_PREFIX}" + json.dumps(unsupported),
    ]
    log_path.write_text("\n".join(lines), encoding="utf-8")

    loaded, coverage = module.load_observations([log_path])
    report = module.aggregate_observations(loaded)
    summary = report["summary"]

    assert len(loaded) == 3
    assert coverage["candidate_lines"] == 5
    assert coverage["accepted"] == 3
    assert coverage["malformed_lines"] == 1
    assert coverage["unsupported_revision"] == 1
    assert summary["token_weighted_cache_read"] == {
        "read_tokens": 50,
        "input_tokens": 400,
        "ratio": 0.125,
        "known_requests": 2,
        "unknown_requests": 1,
    }
    assert summary["request_cache_read"] == {
        "nonzero": 1,
        "known": 2,
        "unknown": 1,
        "ratio": 0.5,
    }
    assert summary["tokens"]["cache_write"] == {"sum": 10, "known": 1, "unknown": 2}
    assert summary["cross_user_turn"] == {
        "observations": 2,
        "relations": {"rebase": 1, "strict_extension": 1},
        "normal_tail_appends": 1,
        "rebases": 1,
        "breakpoint_domains": {"none": 1, "settings": 1},
        "lost_prefix_tokens_estimate": {"sum": 120, "known": 2, "unknown": 0},
    }
    assert report["segments"]["operation"]["conversation"]["observations"] == 2
    assert report["segments"]["comparison_scope"]["cross_user_turn"]["observations"] == 2
    assert report["segments"]["first_divergence_domain"]["settings"]["observations"] == 1
    assert "50 / 400" in module.render_markdown(report)
    assert "estimated lost prefix tokens 120" in module.render_markdown(report)


def test_source_identity_binds_commit_tracked_diff_and_untracked_content(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    module = _load_module()
    (tmp_path / "new.py").write_text("print('new')\n", encoding="utf-8")

    def fake_git_output(_root: Path, *args: str) -> bytes:
        command = tuple(args)
        if command == ("rev-parse", "HEAD"):
            return b"abc123\n"
        if command == ("status", "--porcelain", "-z"):
            return b" M tracked.py\0?? new.py\0"
        if command == ("diff", "--binary", "HEAD", "--"):
            return b"tracked diff"
        if command == ("ls-files", "--others", "--exclude-standard", "-z"):
            return b"new.py\0"
        raise AssertionError(command)

    monkeypatch.setattr(module, "_git_output", fake_git_output)

    identity = module.source_identity(tmp_path)

    assert identity["commit"] == "abc123"
    assert identity["working_tree_dirty"] is True
    assert identity["untracked_files"] == 1
    assert len(identity["overlay_sha256"]) == 64


def test_operation_memory_segments_and_contributor_matrix_are_explicit() -> None:
    module = _load_module()
    observations = [
        _observation(
            input_total=200,
            cache_read=0,
            cache_write=None,
            input_other=None,
            operation="memory_extraction",
            relation="unavailable",
            comparison_scope="unavailable",
            unavailable_reason="no_previous_operation_request",
        ),
        _observation(
            input_total=300,
            cache_read=100,
            cache_write=None,
            input_other=None,
            operation="memory_extraction",
            relation="rebase",
            comparison_scope="within_operation",
            cross_turn_relation="rebase",
            first_divergence_domain="messages",
            lost_prefix_tokens_estimate=80,
        ),
        _observation(
            input_total=400,
            cache_read=100,
            cache_write=None,
            input_other=None,
            operation="conversation",
            relation="rebase",
            comparison_scope="cross_user_turn",
            cross_turn_relation="rebase",
            first_divergence_domain="memory",
            lost_prefix_tokens_estimate=120,
            memory_content_changed=True,
            memory_position_changed=True,
        ),
    ]

    report = module.aggregate_observations(observations)
    operation = report["summary"]["operation_lineage"]
    memory = report["summary"]["memory_breakpoints"]

    assert operation["observations"] == 2
    assert operation["scopes"] == {"unavailable": 1, "within_operation": 1}
    assert operation["unavailable_reasons"] == {"no_previous_operation_request": 1}
    assert operation["lost_prefix_tokens_estimate"] == {
        "sum": 80,
        "known": 1,
        "unknown": 1,
    }
    assert memory["observations"] == 1
    assert memory["first_divergence_memory"] == 1
    assert memory["flags"]["memory_content_changed"]["true"] == 1
    assert memory["lost_prefix_tokens_estimate"]["sum"] == 120
    assert (
        report["segments"]["unavailable_reason"]["no_previous_operation_request"]["observations"]
        == 1
    )

    matrix = report["operation_contributor_matrix"]
    assert set(matrix) == {
        "conversation",
        "context_compaction",
        "memory_extraction",
        "memory_reconciliation",
    }
    expected_contributors = {
        "system",
        "persona",
        "tools",
        "skills",
        "messages",
        "memory",
        "runtime",
        "settings",
        "model-route",
    }
    assert all(set(row) == expected_contributors for row in matrix.values())
    assert all(
        status == "covered" or status == "not_applicable"
        for row in matrix.values()
        for status in row.values()
    )
    markdown = module.render_markdown(report)
    assert "Background operation lineage: 2 observations" in markdown
    assert "Memory breakpoints: 1 observations" in markdown
