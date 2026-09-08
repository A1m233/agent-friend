"""Compare restoration on one immutable in-memory input without executing a turn."""

from __future__ import annotations

import argparse
import dataclasses
import gc
import hashlib
import json
import subprocess
import sys
import time
import types
from pathlib import Path

from agent.paths import sessions_dir
from agent.sessions.rollout import restore_rollout
from agent.sessions.store import JsonlSessionStore

ROOT = Path(__file__).resolve().parents[2]


def fingerprint(result: object) -> str:
    encoded = json.dumps(
        dataclasses.asdict(result), ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def differing_paths(left: object, right: object, path: str = "result") -> list[str]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return [path + ".keys"]
        return [p for key in left for p in differing_paths(left[key], right[key], f"{path}.{key}")]
    if isinstance(left, (tuple, list)):
        if len(left) != len(right):
            return [path + ".length"]
        return [
            p
            for index, (a, b) in enumerate(zip(left, right, strict=True))
            for p in differing_paths(a, b, f"{path}[{index}]")
        ]
    return [] if left == right else [path]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{args.baseline_ref}^{{commit}}"],
        cwd=ROOT,
        text=True,
    ).strip()
    source = subprocess.check_output(
        ["git", "show", f"{revision}:agent/src/agent/sessions/rollout.py"], cwd=ROOT
    )
    baseline = types.ModuleType("agent.sessions._latency_baseline_rollout")
    baseline.__package__ = "agent.sessions"
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, f"git:{revision}:rollout.py", "exec"), baseline.__dict__)
    started = time.perf_counter()
    session = JsonlSessionStore(sessions_dir()).load(args.session_id)
    load_ms = (time.perf_counter() - started) * 1000
    # Source input fingerprint includes all events, not private contents in output.
    source_hash = hashlib.sha256()
    for event in session.events:
        source_hash.update(event.to_jsonl().encode("utf-8") + b"\n")
    rows = []
    results = []
    for name, restore in (("baseline", baseline.restore_rollout), ("candidate", restore_rollout)):
        gc.collect()
        started = time.perf_counter()
        result = restore(session)
        elapsed = (time.perf_counter() - started) * 1000
        row = {
            "variant": name,
            "restoreMs": elapsed,
            "disposition": result.disposition,
            "fingerprint": fingerprint(result),
        }
        rows.append(row)
        results.append(dataclasses.asdict(result))
        print(json.dumps(row), flush=True)
        del result
    differences = differing_paths(*results)
    # Interrupted recovery intentionally rotates this process-local identity.
    # Keep exact differences in the report; never ignore persisted commit identity.
    equivalent = not differences or (
        differences == ["result.cache_epoch.epoch_id"]
        and rows[0]["disposition"] == rows[1]["disposition"] == "interrupted"
        and results[0]["cache_epoch"]["epoch_id"] != results[0]["commit"]["cache_epoch"]["epoch_id"]
        and results[1]["cache_epoch"]["epoch_id"] != results[1]["commit"]["cache_epoch"]["epoch_id"]
    )
    report = {
        "schemaVersion": 1,
        "baselineRef": revision,
        "loadMs": load_ms,
        "inputFingerprint": source_hash.hexdigest(),
        "events": len(session.events),
        "rows": rows,
        "equivalent": equivalent,
        "differingPaths": differences,
        "normalization": "Only interrupted recovery's newly rotated result.cache_epoch.epoch_id may differ; stored commit epoch and every other field must match.",
        "limits": "One same-input sequential comparison; baseline first; current store loader for both; no model calls or user data writes.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return 0 if report["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
