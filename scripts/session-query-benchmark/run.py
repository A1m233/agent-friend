"""Offline synthetic paired measurements for requirement 118 (zero LLM calls).

The old history adapter is read from a fixed Git revision. Both adapters use
the current full-load store implementation, so this comparison isolates read
selection/reuse rather than pretending to recreate the original process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import subprocess
import tempfile
import time
import types
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.memory_search import SessionHistorySource
from agent.sessions import JsonlSessionStore, Session, query_session
from agent.sessions.events import Event
from memory.search import (
    CurrentSessionRecallScope,
    MemorySearchService,
    QueryAnalyzer,
    SearchRequest,
)

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "f563938bd3f63c88a5e7a5b59825e3da33f06303"
SOURCE_PATH = "agent/src/agent/memory_search/session_history_source.py"


def _source_hashes() -> dict[str, str]:
    paths = [
        SOURCE_PATH,
        "memory/src/memory/search/service.py",
        "memory/src/memory/search/execution.py",
    ]
    paths.extend(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in (ROOT / "agent/src/agent/sessions").glob("*.py")
    )
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in sorted(paths)}


class FileMetrics(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if hasattr(record, "session_file_operation"):
            self.records.append(
                {
                    key: getattr(record, key)
                    for key in (
                        "session_file_operation",
                        "byte_count",
                        "wait_ms",
                        "hold_ms",
                        "parse_ms",
                    )
                }
            )

    def summary(self) -> dict[str, Any]:
        return {
            "operations": dict(Counter(row["session_file_operation"] for row in self.records)),
            "bytes": sum(row["byte_count"] for row in self.records),
            **{
                key: sum(row[key] or 0 for row in self.records)
                for key in ("wait_ms", "hold_ms", "parse_ms")
            },
        }


def _fixture(path: Path, payload_mib: int) -> int:
    store = JsonlSessionStore(path)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(17):
        sid = "current" if index == 0 else f"history-{index:02}"
        session = Session.new(
            title=f"Synthetic {index}",
            persona="test",
            model="deepseek/deepseek-chat",
            session_id=sid,
            created_at=start,
        )
        store.create(session)
        ts = start + timedelta(days=30 - index)
        store.append_events(
            sid,
            [
                Event(
                    type="user_message",
                    uuid=f"{sid}-user",
                    ts=ts,
                    payload={"content": f"milestone plan {index}"},
                ),
                Event(
                    type="assistant_message",
                    uuid=f"{sid}-assistant",
                    ts=ts + timedelta(seconds=1),
                    payload={"content": f"milestone response {index}"},
                ),
            ],
        )
        if index == 0:
            # Bounded individual records, unlike an artificial single giant line.
            payload = "x" * (256 * 1024)
            for batch in range(payload_mib):
                store.append_events(
                    sid,
                    [
                        Event(
                            type="model_transcript_item",
                            uuid=f"payload-{batch}-{part}",
                            ts=ts + timedelta(seconds=2),
                            payload={"synthetic": payload},
                        )
                        for part in range(4)
                    ],
                )
    return sum(file.stat().st_size for file in path.glob("*.jsonl"))


def _baseline_source() -> tuple[type, str]:
    raw = subprocess.run(
        ["git", "show", f"{BASELINE}:{SOURCE_PATH}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    module = types.ModuleType("agent.memory_search._requirement_118_baseline")
    module.__package__ = "agent.memory_search"
    exec(compile(raw, f"{BASELINE}:{SOURCE_PATH}", "exec"), module.__dict__)
    return module.SessionHistorySource, hashlib.sha256(raw).hexdigest()


def _signature(result: Any) -> dict[str, Any]:
    return {
        "evidence": [
            {
                "id": item.evidence_id,
                "text": item.text,
                "provenance": list(item.provenance),
                "session_id": item.session_id,
                "excerpts": [asdict(excerpt) for excerpt in item.excerpts],
            }
            for item in result.evidence
        ],
        "truncated": result.truncated,
        "failure_count": len(result.failures),
        "scope_order_status": result.scope_order_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-mib", type=int, default=16)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.payload_mib <= 512 or not 1 <= args.repeats <= 20:
        parser.error("payload-mib must be 1..512, repeats 1..20")
    if args.output.exists():
        parser.error("output already exists; choose a new report path")
    baseline_type, baseline_sha = _baseline_source()
    source_hashes = _source_hashes()
    request = SearchRequest(
        text="milestone",
        owner_user_id="local",
        persona_id="test",
        mode="automatic",
        session_id="current",
        automatic_recall_scope=CurrentSessionRecallScope("current", None),
    )
    # Share tokenizer startup explicitly; only query/store caches are cold below.
    QueryAnalyzer().analyze(request)
    metrics = FileMetrics()
    logger = logging.getLogger("agent.sessions.file_observation")
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(metrics)
    rows: list[dict[str, Any]] = []
    signatures: list[dict[str, Any]] = []
    detail_signatures: list[list[str]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="agent-friend-query-118-") as temporary:
            path = Path(temporary)
            fixture_bytes = _fixture(path, args.payload_mib)
            stores = {"baseline": JsonlSessionStore(path), "candidate": JsonlSessionStore(path)}
            services = {
                "baseline": MemorySearchService([baseline_type(stores["baseline"])]),
                "candidate": MemorySearchService([SessionHistorySource(stores["candidate"])]),
            }
            for repeat in range(args.repeats):
                # Alternate order to make the OS-cache limitation visible.
                order = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
                for name in order:
                    metrics.records.clear()
                    started = time.perf_counter()
                    result = services[name].search(request)
                    rows.append(
                        {
                            "path": name,
                            "repeat": repeat,
                            "duration_ms": (time.perf_counter() - started) * 1000,
                            "source_runs": [asdict(run) for run in result.source_runs],
                            **metrics.summary(),
                        }
                    )
                    signatures.append(_signature(result))
            for repeat in range(args.repeats):
                for name in ("baseline", "candidate"):
                    metrics.records.clear()
                    started = time.perf_counter()
                    if name == "baseline":
                        events = stores[name].load("current").active_events
                    else:
                        events = query_session(stores[name], "current").active_events
                    rows.append(
                        {
                            "path": f"{name}_detail",
                            "repeat": repeat,
                            "duration_ms": (time.perf_counter() - started) * 1000,
                            "event_count": len(events),
                            **metrics.summary(),
                        }
                    )
                    detail_signatures.append([event.to_jsonl() for event in events])
    finally:
        logger.removeHandler(metrics)
        logger.setLevel(previous_level)
    equal = all(signature == signatures[0] for signature in signatures)
    details_equal = all(signature == detail_signatures[0] for signature in detail_signatures)
    report = {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "baseline_commit": BASELINE,
        "baseline_adapter_sha256": baseline_sha,
        "candidate_head": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "candidate_source_sha256": source_hashes,
        "candidate_sources_unchanged": source_hashes == _source_hashes(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
        },
        "fixture": {"kind": "synthetic", "sessions": 17, "bytes": fixture_bytes},
        "repeats": args.repeats,
        "results_equal": equal,
        "detail_events_equal": details_equal,
        "signature_sha256": hashlib.sha256(
            json.dumps(
                signatures[0],
                default=str,
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "measurements": rows,
        "limitations": [
            "Synthetic data; not a reproduction of the historical 136.628-second run.",
            "OS page cache is uncontrolled; repeat 0 is process-query-cache cold, not disk cold.",
            "Both paths use the current store including summary optimizations; baseline only pins the old history adapter, understating the original baseline cost.",
            "File-operation bytes exclude summary reverse scans and are a lower bound.",
            "Result comparison covers ordered IDs/body/provenance/excerpts/scope/truncation; scores may change after eligibility removes rank gaps.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"results_equal": equal, "report": str(args.output.resolve()), "measurements": rows},
            ensure_ascii=False,
        )
    )
    return 0 if equal and details_equal and report["candidate_sources_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
