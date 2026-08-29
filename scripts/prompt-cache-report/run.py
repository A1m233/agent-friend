"""Aggregate versioned prompt-cache observations from governed log files."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.paths import log_dir

from llm_providers import (
    PROMPT_CACHE_OBSERVATION_LOG_PREFIX,
    PROMPT_CACHE_OBSERVATION_SCHEMA,
    PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION,
    PROMPT_CACHE_OPERATION_CONTRIBUTORS,
)


def _nested(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def load_observations(paths: Sequence[Path]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read supported observations and return explicit parser coverage counts."""

    observations: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for path in paths:
        counts["files"] += 1
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            counts["unreadable_files"] += 1
            continue
        for line in lines:
            index = line.find(PROMPT_CACHE_OBSERVATION_LOG_PREFIX)
            if index < 0:
                continue
            counts["candidate_lines"] += 1
            raw = line[index + len(PROMPT_CACHE_OBSERVATION_LOG_PREFIX) :]
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                counts["malformed_lines"] += 1
                continue
            if not isinstance(value, dict):
                counts["malformed_lines"] += 1
                continue
            if value.get("schema") != PROMPT_CACHE_OBSERVATION_SCHEMA:
                counts["foreign_schema"] += 1
                continue
            if value.get("schema_revision") != PROMPT_CACHE_OBSERVATION_SCHEMA_REVISION:
                counts["unsupported_revision"] += 1
                continue
            observations.append(value)
            counts["accepted"] += 1
    return observations, dict(sorted(counts.items()))


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _distribution(values: Iterable[float | None]) -> dict[str, float | int | None]:
    known = [value for value in values if value is not None]
    return {
        "known": len(known),
        "min": min(known) if known else None,
        "p50": _percentile(known, 0.50),
        "p95": _percentile(known, 0.95),
        "max": max(known) if known else None,
    }


_CROSS_TURN_BREAKPOINT_DOMAINS = (
    "runtime",
    "memory",
    "tools",
    "settings",
    "model-route",
    "system",
    "persona",
    "messages",
)
_BREAKPOINT_DOMAIN_ALIASES = {
    "skill": "tools",
    "skills": "tools",
    "model_route": "model-route",
}


def _breakpoint_domain(observation: Mapping[str, Any]) -> str:
    raw = _nested(observation, "structure", "first_divergence_domain")
    if not isinstance(raw, str) or not raw.strip():
        return "unknown"
    normalized = raw.strip()
    return _BREAKPOINT_DOMAIN_ALIASES.get(normalized, normalized)


def _lost_prefix_summary(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    values = [
        _nonnegative_int(_nested(item, "structure", "lost_prefix_tokens_estimate"))
        for item in items
    ]
    return {
        "sum": sum(value for value in values if value is not None),
        "known": sum(value is not None for value in values),
        "unknown": sum(value is None for value in values),
    }


def _cross_turn_breakpoint_opportunities(
    cross_turn: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Attribute each rebase's lost-prefix estimate to one earliest domain."""

    rebases = [
        item for item in cross_turn if _nested(item, "structure", "cross_turn_relation") == "rebase"
    ]
    grouped: dict[str, list[Mapping[str, Any]]] = {
        domain: [] for domain in _CROSS_TURN_BREAKPOINT_DOMAINS
    }
    grouped["unknown"] = []
    for item in rebases:
        grouped.setdefault(_breakpoint_domain(item), []).append(item)

    total = _lost_prefix_summary(rebases)
    domains: dict[str, dict[str, Any]] = {}
    for domain in (*_CROSS_TURN_BREAKPOINT_DOMAINS, "unknown"):
        domain_items = grouped.pop(domain, [])
        lost = _lost_prefix_summary(domain_items)
        domains[domain] = {
            "rebase_observations": len(domain_items),
            "lost_prefix_tokens_estimate": lost,
            "share_of_known_lost_prefix_tokens": (
                lost["sum"] / total["sum"] if total["sum"] > 0 else None
            ),
        }
    for domain in sorted(grouped):
        domain_items = grouped[domain]
        lost = _lost_prefix_summary(domain_items)
        domains[domain] = {
            "rebase_observations": len(domain_items),
            "lost_prefix_tokens_estimate": lost,
            "share_of_known_lost_prefix_tokens": (
                lost["sum"] / total["sum"] if total["sum"] > 0 else None
            ),
        }

    ranking = [
        {
            "domain": domain,
            "rebase_observations": item["rebase_observations"],
            "lost_prefix_tokens_estimate": item["lost_prefix_tokens_estimate"]["sum"],
            "known_estimates": item["lost_prefix_tokens_estimate"]["known"],
            "unknown_estimates": item["lost_prefix_tokens_estimate"]["unknown"],
            "share_of_known_lost_prefix_tokens": item["share_of_known_lost_prefix_tokens"],
        }
        for domain, item in domains.items()
        if item["rebase_observations"] > 0
    ]
    ranking.sort(
        key=lambda item: (
            -item["lost_prefix_tokens_estimate"],
            -item["unknown_estimates"],
            str(item["domain"]),
        )
    )
    return {
        "attribution": "first_divergence_domain",
        "rebase_observations": len(rebases),
        "lost_prefix_tokens_estimate": total,
        "estimate_coverage": {
            "known": total["known"],
            "unknown": total["unknown"],
            "ratio": total["known"] / len(rebases) if rebases else None,
        },
        "domains": domains,
        "ranking": ranking,
    }


def _cross_user_turn_summary(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    cross_turn = [
        item
        for item in observations
        if _nested(item, "structure", "comparison_scope") == "cross_user_turn"
    ]
    relations = Counter(
        str(_nested(item, "structure", "cross_turn_relation") or "unavailable")
        for item in cross_turn
    )
    domains = Counter(
        str(_nested(item, "structure", "first_divergence_domain") or "none") for item in cross_turn
    )
    lost_estimates = [
        _nonnegative_int(_nested(item, "structure", "lost_prefix_tokens_estimate"))
        for item in cross_turn
    ]
    normal_tail_appends = sum(
        _nested(item, "structure", "cross_turn_relation") == "strict_extension"
        and _nested(item, "structure", "change_flags", "messages_appended") is True
        and _nested(item, "structure", "change_flags", "messages_rewritten") is not True
        for item in cross_turn
    )
    return {
        "observations": len(cross_turn),
        "relations": dict(sorted(relations.items())),
        "normal_tail_appends": normal_tail_appends,
        "rebases": relations.get("rebase", 0),
        "breakpoint_domains": dict(sorted(domains.items())),
        "lost_prefix_tokens_estimate": {
            "sum": sum(value for value in lost_estimates if value is not None),
            "known": sum(value is not None for value in lost_estimates),
            "unknown": sum(value is None for value in lost_estimates),
        },
        "breakpoint_opportunities": _cross_turn_breakpoint_opportunities(cross_turn),
    }


def _operation_lineage_summary(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    operation_names = {
        "context_compaction",
        "memory_extraction",
        "memory_reconciliation",
    }
    items = [
        item for item in observations if _nested(item, "identity", "operation") in operation_names
    ]
    scopes = Counter(
        str(_nested(item, "structure", "comparison_scope") or "unavailable") for item in items
    )
    relations = Counter(
        str(_nested(item, "structure", "cross_turn_relation") or "unavailable") for item in items
    )
    unavailable_reasons = Counter(
        str(_nested(item, "structure", "unavailable_reason") or "none")
        for item in items
        if _nested(item, "structure", "comparison_scope") == "unavailable"
    )
    lost_estimates = [
        _nonnegative_int(_nested(item, "structure", "lost_prefix_tokens_estimate"))
        for item in items
    ]
    return {
        "observations": len(items),
        "scopes": dict(sorted(scopes.items())),
        "relations": dict(sorted(relations.items())),
        "unavailable_reasons": dict(sorted(unavailable_reasons.items())),
        "lost_prefix_tokens_estimate": {
            "sum": sum(value for value in lost_estimates if value is not None),
            "known": sum(value is not None for value in lost_estimates),
            "unknown": sum(value is None for value in lost_estimates),
        },
    }


def _memory_breakpoint_summary(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    flag_names = (
        "memory_presence_changed",
        "memory_content_changed",
        "memory_position_changed",
    )
    cross_turn = [
        item
        for item in observations
        if _nested(item, "structure", "comparison_scope") == "cross_user_turn"
    ]
    items = [
        item
        for item in cross_turn
        if _nested(item, "structure", "first_divergence_domain") == "memory"
        or any(_nested(item, "structure", "change_flags", flag) is True for flag in flag_names)
    ]
    attributed = [
        item
        for item in cross_turn
        if _nested(item, "structure", "cross_turn_relation") == "rebase"
        and _breakpoint_domain(item) == "memory"
    ]
    shadowed = [
        item
        for item in items
        if _nested(item, "structure", "cross_turn_relation") == "rebase"
        and _breakpoint_domain(item) not in {"memory", "unknown"}
    ]
    return {
        "attribution": "first_divergence_domain",
        "observations": len(items),
        "first_divergence_memory": len(attributed),
        "shadowed_by_earlier_breakpoint": len(shadowed),
        "flags": {
            flag: {
                "true": sum(
                    _nested(item, "structure", "change_flags", flag) is True for item in items
                ),
                "unknown": sum(
                    _nested(item, "structure", "change_flags", flag) is None for item in items
                ),
            }
            for flag in flag_names
        },
        "lost_prefix_tokens_estimate": _lost_prefix_summary(attributed),
    }


def _summary(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = ("input_total", "cache_read", "cache_write", "input_other", "output")
    values = {
        field: [_nonnegative_int(_nested(item, "usage", field)) for item in observations]
        for field in fields
    }
    known_pairs = [
        (input_total, cache_read)
        for input_total, cache_read in zip(values["input_total"], values["cache_read"], strict=True)
        if input_total is not None and cache_read is not None
    ]
    read_tokens = sum(cache_read for _, cache_read in known_pairs)
    input_tokens = sum(input_total for input_total, _ in known_pairs)
    known_cache_requests = [value for value in values["cache_read"] if value is not None]
    status_counts = Counter(
        str(_nested(item, "outcome", "status") or "unknown") for item in observations
    )
    cost_values = [
        _nonnegative_number(_nested(item, "outcome", "response_cost")) for item in observations
    ]
    known_costs = [value for value in cost_values if value is not None]
    return {
        "observations": len(observations),
        "status": dict(sorted(status_counts.items())),
        "tokens": {
            field: {
                "sum": sum(value for value in field_values if value is not None),
                "known": sum(value is not None for value in field_values),
                "unknown": sum(value is None for value in field_values),
            }
            for field, field_values in values.items()
        },
        "token_weighted_cache_read": {
            "read_tokens": read_tokens,
            "input_tokens": input_tokens,
            "ratio": read_tokens / input_tokens if input_tokens > 0 else None,
            "known_requests": len(known_pairs),
            "unknown_requests": len(observations) - len(known_pairs),
        },
        "request_cache_read": {
            "nonzero": sum(value > 0 for value in known_cache_requests),
            "known": len(known_cache_requests),
            "unknown": len(observations) - len(known_cache_requests),
            "ratio": (
                sum(value > 0 for value in known_cache_requests) / len(known_cache_requests)
                if known_cache_requests
                else None
            ),
        },
        "reconciled_input_buckets": {
            "true": sum(
                bool(_nested(item, "usage", "buckets_reconciled")) for item in observations
            ),
            "total": len(observations),
        },
        "actual_response_cost": {
            "sum": sum(known_costs),
            "known": len(known_costs),
            "unknown": len(observations) - len(known_costs),
        },
        "timing_ms": {
            name: _distribution(
                _nonnegative_number(_nested(item, "timing", name)) for item in observations
            )
            for name in ("stream_open_ms", "first_chunk_ms", "total_ms")
        },
        "cross_user_turn": _cross_user_turn_summary(observations),
        "operation_lineage": _operation_lineage_summary(observations),
        "memory_breakpoints": _memory_breakpoint_summary(observations),
    }


def _request_size_bucket(observation: Mapping[str, Any]) -> str:
    value = _nonnegative_int(_nested(observation, "usage", "input_total"))
    if value is None:
        return "unknown"
    if value == 0:
        return "0"
    if value <= 1_000:
        return "1-1k"
    if value <= 8_000:
        return "1k-8k"
    if value <= 32_000:
        return "8k-32k"
    if value <= 128_000:
        return "32k-128k"
    return "128k+"


_SEGMENT_PATHS: dict[str, tuple[str, ...]] = {
    "provider": ("model", "provider"),
    "requested_model": ("model", "requested"),
    "response_model": ("model", "response"),
    "surface": ("identity", "surface"),
    "operation": ("identity", "operation"),
    "call_kind": ("identity", "call_kind"),
    "call_index": ("identity", "call_index"),
    "final_wire_relation": ("structure", "final_wire_relation"),
    "comparison_scope": ("structure", "comparison_scope"),
    "cross_turn_relation": ("structure", "cross_turn_relation"),
    "first_divergence_domain": ("structure", "first_divergence_domain"),
    "unavailable_reason": ("structure", "unavailable_reason"),
    "memory_presence_changed": ("structure", "change_flags", "memory_presence_changed"),
    "memory_content_changed": ("structure", "change_flags", "memory_content_changed"),
    "memory_position_changed": ("structure", "change_flags", "memory_position_changed"),
    "rebase_reason": ("structure", "rebase_reason"),
    "interpretation": ("outcome", "interpretation"),
    "accounting": ("usage", "accounting"),
}


def aggregate_observations(observations: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate totals and independent dimensions with identical math."""

    segments: dict[str, dict[str, Any]] = {}
    for dimension, path in _SEGMENT_PATHS.items():
        grouped: dict[str, list[dict[str, Any]]] = {}
        for observation in observations:
            raw = _nested(observation, *path)
            key = str(raw) if raw is not None and raw != "" else "unknown"
            grouped.setdefault(key, []).append(observation)
        segments[dimension] = {key: _summary(items) for key, items in sorted(grouped.items())}
    request_sizes: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        request_sizes.setdefault(_request_size_bucket(observation), []).append(observation)
    segments["request_size"] = {
        key: _summary(items) for key, items in sorted(request_sizes.items())
    }
    return {
        "schema": "agent-friend.prompt-cache-report",
        "schema_revision": 2,
        "summary": _summary(observations),
        "segments": segments,
        "operation_contributor_matrix": PROMPT_CACHE_OPERATION_CONTRIBUTORS,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = _nested(report, "summary") or {}
    weighted = _nested(summary, "token_weighted_cache_read") or {}
    requests = _nested(summary, "request_cache_read") or {}
    cross_turn = _nested(summary, "cross_user_turn") or {}
    operation_lineage = _nested(summary, "operation_lineage") or {}
    memory_breakpoints = _nested(summary, "memory_breakpoints") or {}
    lost = cross_turn.get("lost_prefix_tokens_estimate", {})
    opportunities = cross_turn.get("breakpoint_opportunities", {})
    lines = [
        "# Prompt cache report",
        "",
        f"- Observations: {summary.get('observations', 0)}",
        (
            "- Token-weighted cache read: "
            f"{weighted.get('read_tokens', 0)} / {weighted.get('input_tokens', 0)} "
            f"= {_format_ratio(weighted.get('ratio'))} "
            f"(known requests {weighted.get('known_requests', 0)}, "
            f"unknown {weighted.get('unknown_requests', 0)})"
        ),
        (
            "- Requests with non-zero cache read: "
            f"{requests.get('nonzero', 0)} / {requests.get('known', 0)} "
            f"= {_format_ratio(requests.get('ratio'))} "
            f"(unknown {requests.get('unknown', 0)})"
        ),
        (
            "- Cross-user-turn rebases: "
            f"{cross_turn.get('rebases', 0)} / {cross_turn.get('observations', 0)}; "
            f"normal tail appends {cross_turn.get('normal_tail_appends', 0)}; "
            f"estimated lost prefix tokens {lost.get('sum', 0)} "
            f"(known {lost.get('known', 0)}, unknown {lost.get('unknown', 0)})"
        ),
        (
            "- Background operation lineage: "
            f"{operation_lineage.get('observations', 0)} observations; "
            f"scopes {operation_lineage.get('scopes', {})}; "
            "estimated lost prefix tokens "
            f"{operation_lineage.get('lost_prefix_tokens_estimate', {}).get('sum', 0)}"
        ),
        (
            "- Memory breakpoints: "
            f"{memory_breakpoints.get('observations', 0)} observations; "
            f"first divergence memory {memory_breakpoints.get('first_divergence_memory', 0)}; "
            "shadowed by earlier breakpoint "
            f"{memory_breakpoints.get('shadowed_by_earlier_breakpoint', 0)}; "
            "estimated lost prefix tokens "
            f"{memory_breakpoints.get('lost_prefix_tokens_estimate', {}).get('sum', 0)}"
        ),
        (
            "- Cross-user-turn lost-prefix estimate coverage: "
            f"{opportunities.get('estimate_coverage', {}).get('known', 0)} / "
            f"{opportunities.get('rebase_observations', 0)} "
            f"= {_format_ratio(opportunities.get('estimate_coverage', {}).get('ratio'))} "
            f"(unknown {opportunities.get('estimate_coverage', {}).get('unknown', 0)})"
        ),
        "",
        "## Cross-user-turn breakpoint opportunities",
        "",
        "| Rank | Breakpoint domain | Rebase calls | Estimated lost prefix tokens | "
        "Share of known lost tokens | Known estimates | Unknown estimates |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, item in enumerate(opportunities.get("ranking", []), start=1):
        lines.append(
            f"| {rank} | {item['domain']} | {item['rebase_observations']} | "
            f"{item['lost_prefix_tokens_estimate']} | "
            f"{_format_ratio(item['share_of_known_lost_prefix_tokens'])} | "
            f"{item['known_estimates']} | {item['unknown_estimates']} |"
        )
    lines.extend(
        [
            "",
            "## Input buckets",
            "",
            "| Bucket | Sum | Known | Unknown |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    tokens = summary.get("tokens", {})
    for field in ("input_total", "cache_read", "cache_write", "input_other", "output"):
        item = tokens.get(field, {})
        lines.append(
            f"| {field} | {item.get('sum', 0)} | {item.get('known', 0)} | "
            f"{item.get('unknown', 0)} |"
        )
    for dimension in (
        "provider",
        "operation",
        "interpretation",
        "final_wire_relation",
        "comparison_scope",
        "cross_turn_relation",
        "first_divergence_domain",
        "unavailable_reason",
    ):
        lines.extend(
            [
                "",
                f"## By {dimension}",
                "",
                "| Value | Calls | Read / Input | Ratio |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        groups = _nested(report, "segments", dimension) or {}
        for key, item in groups.items():
            metric = item["token_weighted_cache_read"]
            lines.append(
                f"| {key} | {item['observations']} | {metric['read_tokens']} / "
                f"{metric['input_tokens']} | {_format_ratio(metric['ratio'])} |"
            )
    return "\n".join(lines) + "\n"


def _format_ratio(value: Any) -> str:
    return f"{float(value):.2%}" if isinstance(value, (int, float)) else "unavailable"


def _default_inputs() -> list[Path]:
    return sorted(path for path in log_dir().glob("agent_bridge.log*") if path.is_file())


def _git_output(repo_root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def source_identity(repo_root: Path) -> dict[str, Any]:
    """Bind a report to the current commit and a content hash of its overlay."""

    commit_raw = _git_output(repo_root, "rev-parse", "HEAD")
    status = _git_output(repo_root, "status", "--porcelain", "-z")
    tracked_diff = _git_output(repo_root, "diff", "--binary", "HEAD", "--")
    untracked = _git_output(repo_root, "ls-files", "--others", "--exclude-standard", "-z")
    if commit_raw is None or status is None or tracked_diff is None or untracked is None:
        return {"mode": "unavailable"}
    digest = hashlib.sha256()
    digest.update(b"prompt-cache-report-overlay-v1\0")
    digest.update(tracked_diff)
    untracked_count = 0
    for raw_path in sorted(item for item in untracked.split(b"\0") if item):
        path = repo_root / raw_path.decode("utf-8", errors="surrogateescape")
        if not path.is_file():
            continue
        untracked_count += 1
        digest.update(raw_path)
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"unreadable")
    return {
        "mode": "git",
        "commit": commit_raw.decode("ascii", errors="replace").strip(),
        "working_tree_dirty": bool(status),
        "overlay_sha256": digest.hexdigest(),
        "untracked_files": untracked_count,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, default=[])
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    paths = list(args.input) or _default_inputs()
    observations, parser_coverage = load_observations(paths)
    report = aggregate_observations(observations)
    report["parser_coverage"] = parser_coverage
    report["input_files"] = [path.name for path in paths]
    report["source_identity"] = source_identity(Path(__file__).resolve().parents[2])
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else render_markdown(report)
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
