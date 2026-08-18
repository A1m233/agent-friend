"""写入 agent_eval reports / baselines JSON。"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .report import CaseRunResult, summarize
from .schema import CaseSetMeta, Contract
from .selection import Selection

_SCHEMA_VERSION = 4
_GIT_TIMEOUT_SEC = 5


@dataclass(frozen=True)
class ProviderRunInfo:
    """写入报告的 provider 信息。"""

    model: str
    api_base: str | None
    defaults: Mapping[str, Any]
    context_window: int | None


@dataclass(frozen=True)
class ToolRunInfo:
    """写入报告的工具可用性信息。"""

    tavily_key_present: bool
    web_search_available: bool
    recall_past_chats_available: bool


@dataclass(frozen=True)
class ReportRun:
    """一次 agent eval 运行的元数据。"""

    started_at: datetime
    ended_at: datetime
    selection: Selection
    provider: ProviderRunInfo
    tools: ToolRunInfo
    case_set: CaseSetMeta
    note: str = ""


def _short_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "nogit"
    if result.returncode != 0:
        return "nogit"
    return result.stdout.strip() or "nogit"


def _working_tree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def _safe_label(label: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", label.strip())
    return safe.strip("-") or "selection"


def _filename(started_at: datetime, label: str, sha: str) -> str:
    stamp = started_at.strftime("%Y-%m-%dT%H-%M-%S")
    return f"{stamp}-{sha}-{_safe_label(label)}.json"


def _serialize_results(
    contracts_run: list[Contract],
    results: dict[str, list[CaseRunResult]],
) -> list[dict[str, object]]:
    contracts_payload: list[dict[str, object]] = []
    for contract in contracts_run:
        items = results[contract.id]
        passed = sum(1 for item in items if item.verdict.passed)
        contracts_payload.append(
            {
                "id": contract.id,
                "name": contract.name,
                "expected_behavior": contract.expected_behavior,
                "total": len(items),
                "passed": passed,
                "failed": len(items) - passed,
                "pass_rate": (passed / len(items)) if items else 0,
                "cases": [
                    {
                        "id": item.case.id,
                        "query": item.case.query,
                        "facet": item.case.facet,
                        "source": item.case.source,
                        "surface": item.case.surface,
                        "self_presentation_mode": item.case.self_presentation_mode,
                        "passed": item.verdict.passed,
                        "reason": item.verdict.reason,
                        "tool_calls": [
                            {
                                "name": tool_call.name,
                                "args": tool_call.args,
                                "result_text": tool_call.result_text,
                                "is_error": tool_call.is_error,
                                "duration_seconds": tool_call.duration_seconds,
                            }
                            for tool_call in item.trace.tool_calls
                        ],
                        "skill_activations": [
                            {
                                "status": activation.status,
                                "skill_id": activation.skill_id,
                                "name": activation.name,
                                "source": activation.source,
                                "invocation_kind": activation.invocation_kind,
                                "reason": activation.reason,
                                "error_code": activation.error_code,
                                "error_message": activation.error_message,
                            }
                            for activation in item.trace.skill_activations
                        ],
                        "assistant_segments": item.trace.assistant_segments,
                        "skill_catalog_observation": (
                            {
                                "snapshot_entries": [
                                    {
                                        "name": entry.name,
                                        "source": entry.source,
                                        "id": entry.id,
                                    }
                                    for entry in item.trace.skill_catalog_observation.snapshot_entries
                                ],
                                "snapshot_coverage": (
                                    item.trace.skill_catalog_observation.snapshot_coverage
                                ),
                                "catalog_fingerprint": (
                                    item.trace.skill_catalog_observation.catalog_fingerprint
                                ),
                                "render_fingerprint": (
                                    item.trace.skill_catalog_observation.render_fingerprint
                                ),
                                "request_entries": [
                                    {
                                        "name": entry.name,
                                        "source": entry.source,
                                        "id": entry.id,
                                    }
                                    for entry in item.trace.skill_catalog_observation.request_entries
                                ],
                                "request_coverage": (
                                    item.trace.skill_catalog_observation.request_coverage
                                ),
                                "request_content_fingerprint": (
                                    item.trace.skill_catalog_observation.request_content_fingerprint
                                ),
                                "request_message_index": (
                                    item.trace.skill_catalog_observation.request_message_index
                                ),
                                "preflight_passed": (
                                    item.trace.skill_catalog_observation.preflight_passed
                                ),
                            }
                            if item.trace.skill_catalog_observation is not None
                            else None
                        ),
                        "final_text": item.trace.final_text,
                        "stop_reason": item.trace.stop_reason,
                        "error": item.trace.error,
                    }
                    for item in items
                ],
            }
        )
    return contracts_payload


def write_report(
    *,
    out_dir: Path,
    label: str,
    contracts_run: list[Contract],
    results: dict[str, list[CaseRunResult]],
    run: ReportRun,
) -> Path:
    """把本次运行结果写成 JSON，返回文件路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = _short_sha()
    dirty = _working_tree_dirty()
    path = out_dir / _filename(run.started_at, label, sha)
    duration = (run.ended_at - run.started_at).total_seconds()
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "run": {
            "started_at": run.started_at.isoformat(),
            "ended_at": run.ended_at.isoformat(),
            "duration_seconds": round(duration, 3),
            "git_commit": sha,
            "working_tree_dirty": dirty,
            "note": run.note,
            "selection": {
                "contract": run.selection.contract,
                "case": run.selection.case_id,
                "limit_cases": run.selection.limit_cases,
            },
            "provider": {
                "model": run.provider.model,
                "api_base": run.provider.api_base,
                "defaults": dict(run.provider.defaults),
                "context_window": run.provider.context_window,
            },
            "tools": {
                "tavily_key_present": run.tools.tavily_key_present,
                "web_search_available": run.tools.web_search_available,
                "recall_past_chats_available": run.tools.recall_past_chats_available,
            },
            "case_set": {
                "digest": run.case_set.digest,
                "total_contracts": run.case_set.total_contracts,
                "total_cases": run.case_set.total_cases,
                "contract_counts": run.case_set.contract_counts,
            },
        },
        "summary": summarize(results),
        "contracts": _serialize_results(contracts_run, results),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
