"""Offline derived-cache measurements on disposable copies of an explicit journal.

Each repeat starts without derived files, then a separate process reuses them.
Source bytes are never modified. OS caches are uncontrolled. No provider is built.
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
import hashlib
import json
import logging
import os
import platform
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("agent", "llm_providers", "memory", "runtime_config")


def digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def digest_value(value: Any) -> str:
    def encode_extra(item: Any) -> Any:
        if isinstance(item, (set, frozenset)):
            return sorted(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"unhandled digest type: {type(item).__name__}")

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode_extra).encode("utf-8")
    ).hexdigest()


def source_manifest(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): digest_file(path)
        for package in PACKAGES
        for path in sorted((directory / package / "src").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def snapshot_sources(destination: Path, revision: str | None) -> dict[str, Any]:
    """Freeze measured source: later concurrent edits cannot mix versions in a run."""
    if revision is not None:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            cwd=ROOT,
            text=True,
        ).strip()
        archive = destination / "source.zip"
        subprocess.run(
            [
                "git",
                "archive",
                "--format=zip",
                f"--output={archive}",
                commit,
                *(f"{package}/src" for package in PACKAGES),
            ],
            cwd=ROOT,
            check=True,
        )
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                resolved = (destination / info.filename).resolve()
                if not resolved.is_relative_to(destination.resolve()):
                    raise ValueError("source_archive_escaped_destination")
            bundle.extractall(destination)
        archive.unlink()
        return {"kind": "git", "commit": commit, "files": source_manifest(destination)}
    before = source_manifest(ROOT)
    for package in PACKAGES:
        shutil.copytree(
            ROOT / package / "src",
            destination / package / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    after = source_manifest(ROOT)
    copied = source_manifest(destination)
    if before != after or before != copied:
        raise ValueError("source_changed_during_freeze")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    return {"kind": "workspace", "base_commit": commit, "files": copied}


def event_digest(events: Any, *, canonical: bool = False) -> str:
    digest = hashlib.sha256()
    for event in events:
        text = event.to_jsonl()
        if canonical:
            text = json.dumps(
                json.loads(text), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        digest.update(text.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def validate_repeat(operation: str, conditions: list[dict[str, Any]]) -> list[str]:
    """Compare four accesses within one source copy, including its stable epoch."""
    rows = [row for condition in conditions for row in condition["rows"]]
    if len(rows) != 4:
        raise ValueError("cold_cache_equivalence_requires_four_accesses")
    bounded_history = operation == "history" and not all(
        row.get("read_kind") == "legacy_query_then_page" for row in rows
    )
    fields = ["history_page_sha256" if bounded_history else "canonical_business_sha256"]
    if operation == "execution" and all(row.get("exact_digest_comparable") for row in rows):
        fields.append("restore_sha256")
    for field in fields:
        values = [row.get(field) for row in rows]
        if not all(isinstance(value, str) and value for value in values) or len(set(values)) != 1:
            raise ValueError(f"cold_cache_equivalence_mismatch:{field}")
    return fields


def history_measurements(
    payload: dict[str, Any], *, item_limit: int = 80, byte_limit: int = 262144
) -> dict[str, Any]:
    """Content-free page checks; pending requests are valid unpaired history."""
    items = payload["events"]
    compact_bytes = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )
    if len(items) > item_limit or compact_bytes > byte_limit:
        raise ValueError("bounded_history_exceeds_response_limit")
    if len({item["uuid"] for item in items}) != len(items):
        raise ValueError("bounded_history_duplicate_items")
    requests = paired = deferred = 0

    def correlation(item: dict[str, Any]) -> str | None:
        data = item.get("payload", {})
        return data.get("event_tool_call_id") or data.get("tool_call_id")

    def run_id(item: dict[str, Any]) -> str | None:
        return (
            item.get("run", {}).get("run_id")
            or item.get("meta", {}).get("run_id")
            or item.get("payload", {}).get("run_id")
        )

    for item in items:
        deferred += item.get("body", {}).get("state") == "deferred"
        if item["type"] == "tool_call_result":
            raise ValueError("bounded_history_has_standalone_tool_result")
        if item["type"] != "tool_call_request":
            continue
        requests += 1
        result = item.get("result_event")
        if result is None:
            continue
        if (
            result.get("type") != "tool_call_result"
            or not correlation(item)
            or correlation(item) != correlation(result)
            or run_id(item) != run_id(result)
        ):
            raise ValueError("bounded_history_tool_pair_mismatch")
        paired += 1
        deferred += result.get("body", {}).get("state") == "deferred"
    return {
        "read_kind": "bounded_history",
        "history_page_sha256": digest_value(payload),
        # Retain the original pilot field/serialization for comparisons.
        "response_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        "response_bytes_encoding": "json_default_separators_with_spaces",
        "compact_response_bytes": compact_bytes,
        "compact_response_encoding": "utf8_json_no_ascii_escape_compact_separators",
        "bridge_metadata_headroom_bytes": byte_limit - compact_bytes,
        "display_items": len(items),
        "tool_requests": requests,
        "paired_results": paired,
        "pending_tool_requests": requests - paired,
        "deferred_bodies": deferred,
        "item_limit": item_limit,
        "byte_limit": byte_limit,
        "tool_pair_validation": "passed",
    }


def child(directory: Path, sources: Path, session_id: str, operation: str) -> dict[str, Any]:
    def denied(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("cold_cache_probe_forbids_network")

    socket.socket.connect = denied  # type: ignore[method-assign]
    socket.socket.connect_ex = denied  # type: ignore[method-assign]
    socket.create_connection = denied
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["AGENT_FRIEND_DATA_DIR"] = str(directory.parent)
    os.environ["AGENT_FRIEND_LOG_DIR"] = str(directory.parent / "logs")
    for name in PACKAGES:
        sys.path.insert(0, str(sources / name / "src"))
    # Import actual storage modules without constructing application services.
    for name, relative in (
        ("agent", "agent/src/agent"),
        ("agent.sessions", "agent/src/agent/sessions"),
    ):
        module = types.ModuleType(name)
        module.__path__ = [str(sources / relative)]
        sys.modules[name] = module
    logging.getLogger().addHandler(logging.NullHandler())
    from agent.sessions.store import JsonlSessionStore

    memory = runpy.run_path(str(ROOT / "scripts/session-memory/run.py"))["memory"]
    store = JsonlSessionStore(directory)
    rows = []
    # The second call is same-store reuse, NOT a resident SessionManager runtime.
    for access in ("first_call", "same_store_repeat"):
        before = memory()
        started = time.perf_counter()
        if operation == "execution":
            value = store.load_execution(session_id)
        elif operation == "history" and hasattr(store, "history_page"):
            value = store.history_page(session_id)
        else:
            value = store.query(session_id)
        elapsed_ms = (time.perf_counter() - started) * 1000
        observed = memory()
        row: dict[str, Any] = {
            "access": access,
            "elapsed_ms": elapsed_ms,
            "memory_before": before,
            "memory_after": observed,
        }
        if operation == "execution":
            restored = value.restored_rollout
            row.update(
                {
                    "business_events": len(value.active_events),
                    "business_sha256": event_digest(value.active_events),
                    "canonical_business_sha256": event_digest(value.active_events, canonical=True),
                    "disposition": restored.disposition if restored is not None else None,
                    "restore_sha256": digest_value(dataclasses.asdict(restored))
                    if restored is not None
                    else None,
                    "exact_digest_comparable": restored is not None
                    and restored.disposition == "exact",
                }
            )
            observer = getattr(store, "execution_cache_observation", None)
            if callable(observer):
                observation = observer(session_id)
                row["cache_observation"] = (
                    dataclasses.asdict(observation)
                    if dataclasses.is_dataclass(observation)
                    else observation
                )
        elif operation == "history" and hasattr(store, "history_page"):
            payload = dataclasses.asdict(value) if dataclasses.is_dataclass(value) else value
            row.update(history_measurements(payload))
        else:
            events, before_cursor = value.page_events(turn_limit=30)
            body = [json.loads(event.to_jsonl()) for event in events]
            row.update(
                {
                    "read_kind": "legacy_query_then_page",
                    "business_events": len(value.active_events),
                    "business_sha256": event_digest(value.active_events),
                    "canonical_business_sha256": event_digest(value.active_events, canonical=True),
                    "page_events": len(events),
                    "has_older": before_cursor is not None,
                    "response_event_bytes": len(
                        json.dumps(body, ensure_ascii=False).encode("utf-8")
                    ),
                }
            )
        row["derived_bytes"] = sum(
            path.stat().st_size
            for path in directory.rglob("*")
            if path.is_file() and path.name != f"{session_id}.jsonl"
        )
        rows.append(row)
        del value
        gc.collect()
    return {"rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--source-ref", help="Trusted repository commit; omit for frozen workspace")
    parser.add_argument("--operation", choices=("query", "history", "execution"), default="query")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--sources", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--session-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child(args.directory, args.sources, args.session_id, args.operation)))
        return 0
    if args.fixture is None or not args.fixture.is_file():
        parser.error("fixture must be an explicit existing JSONL file")
    if args.output is None or args.output.exists():
        parser.error("output must be a new report file")
    if not 1 <= args.repeat <= 10:
        parser.error("repeat must be 1..10")
    source = args.fixture.resolve(strict=True)
    before = source.stat()
    with source.open("rb") as handle:
        meta = json.loads(handle.readline())
    if meta.get("type") != "session_meta":
        parser.error("fixture requires session_meta first")
    session_id = str(UUID(meta["uuid"]))
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "offline_cold_cache",
        "operation": args.operation,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "harness_sha256": digest_file(Path(__file__)),
        "rows": [],
        "equivalence_checks": [],
        "limits": [
            "isolated source storage modules; no manager, dynamic preparation, UI or provider",
            "OS file caches uncontrolled; fixture copy and verification warm disk caches",
            "same-store repeat is not a resident execution runtime",
            "restore digest comparison only meaningful for exact states (degraded epochs may vary)",
            "legacy query page event bytes exclude REST metadata",
            "memory includes each worker's imports and high water mark",
            "history compact bytes use HTTP JSON serialization but exclude bridge-added metadata; headroom is reported",
            "history response_bytes retains spaced JSON counting for compatibility with earlier pilots",
            "business_sha256 retains legacy JSON key order; canonical_business_sha256 is the semantic equality check",
            "history page digests compare within each repeat; source-bound epochs can differ between copies",
        ],
    }
    # Only this newly owned temporary directory is recursively cleaned up.
    with tempfile.TemporaryDirectory(prefix="af-cold-cache-") as temporary:
        work = Path(temporary).resolve()
        sources = work / "sources"
        sources.mkdir()
        report["source"] = snapshot_sources(sources, args.source_ref)
        fixture = work / "fixture.jsonl"
        with source.open("rb") as original, fixture.open("xb") as copied:
            shutil.copyfileobj(original, copied)
        fingerprint = digest_file(fixture)
        if digest_file(source) != fingerprint:
            raise ValueError("fixture_changed_during_copy")
        report["fixture"] = {"bytes": fixture.stat().st_size, "sha256": fingerprint}
        for repeat in range(args.repeat):
            directory = work / f"repeat-{repeat + 1}" / "sessions"
            directory.mkdir(parents=True)
            journal = directory / f"{session_id}.jsonl"
            shutil.copyfile(fixture, journal)
            conditions: list[dict[str, Any]] = []
            for condition in ("no_derived_cache", "new_process_retained_cache"):
                result = subprocess.check_output(
                    [
                        sys.executable,
                        "-B",
                        str(Path(__file__).resolve()),
                        "--child",
                        "--directory",
                        str(directory),
                        "--sources",
                        str(sources),
                        "--session-id",
                        session_id,
                        "--operation",
                        args.operation,
                    ],
                    cwd=ROOT,
                    text=True,
                    encoding="utf-8",
                    timeout=600,
                )
                row = {"repeat": repeat + 1, "condition": condition, **json.loads(result)}
                report["rows"].append(row)
                conditions.append(row)
                print(json.dumps(row), flush=True)
            fields = validate_repeat(args.operation, conditions)
            report["equivalence_checks"].append(
                {"repeat": repeat + 1, "fields": fields, "accesses": 4, "passed": True}
            )
            if digest_file(journal) != fingerprint:
                raise ValueError("measurement_modified_journal")
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or digest_file(source) != fingerprint:
            raise ValueError("original_fixture_changed_during_measurement")
    report["original_unchanged"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
