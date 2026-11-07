"""Synthetic, offline first-page growth checks; never reads real user sessions."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import hashlib
import json
import os
import platform
import socket
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=20)
    args = parser.parse_args()
    if args.output.exists() or args.repeats < 2:
        parser.error("output must not exist; repeats must be at least 2")
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    # Windows' event-loop self-pipe needs a local socket before network denial.
    from offline_probe import close_offline_loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    atexit.register(close_offline_loop, loop)

    def deny(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("responsiveness probe forbids network")

    socket.socket.connect = deny  # type: ignore[method-assign]
    socket.create_connection = deny  # type: ignore[assignment]
    from agent import Event, JsonlSessionStore, Session

    def sources() -> dict[str, str]:
        return {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "agent/src/agent/sessions").rglob("*.py"))
        }

    identity = sources()
    rows = []
    with tempfile.TemporaryDirectory(prefix="af-172-latency-") as temporary:
        for prefix_turns, internal_mib in ((100, 0), (10000, 0), (100000, 0), (100, 64)):
            directory = Path(temporary) / f"{prefix_turns}-{internal_mib}"
            store = JsonlSessionStore(directory)
            session = Session.new(
                "Synthetic", "default", "deepseek/deepseek-chat", session_id="probe"
            )
            store.create(session)
            path = directory / "probe.jsonl"
            with path.open("a", encoding="utf-8", newline="\n") as file:
                for index in range(prefix_turns + 10):
                    suffix = (
                        f"prefix-{index}"
                        if index < prefix_turns
                        else f"recent-{index - prefix_turns}"
                    )
                    for kind in ("user_message", "assistant_message"):
                        event = Event(
                            kind,
                            f"{kind}-{suffix}",
                            session.created_at,  # type: ignore[arg-type]
                            {"content": suffix + " " + "x" * 128},
                        )
                        file.write(event.to_jsonl() + "\n")
                if internal_mib:
                    file.write(
                        Event(
                            "model_transcript_item",
                            "internal",
                            session.created_at,
                            {"value": "x" * (internal_mib * 1024**2)},
                        ).to_jsonl()
                        + "\n"
                    )
            store.discard_business_index("probe")
            for mode in ("legacy_first", "legacy_reopen", "indexed"):
                if mode == "indexed":
                    store.query("probe")  # fixture setup is outside timed reads
                elapsed = []
                for _ in range(args.repeats):
                    if mode == "legacy_first":
                        store.discard_business_index("probe")
                    fresh = JsonlSessionStore(directory)
                    fresh._read_records = deny  # type: ignore[method-assign]
                    fresh.query = deny  # type: ignore[method-assign]
                    started = time.perf_counter()
                    page = fresh.history_window("probe", turn_limit=10)
                    payload = [json.loads(event.to_jsonl()) for event in page.active_events]
                    encoded = json.dumps(payload).encode()
                    elapsed.append((time.perf_counter() - started) * 1000)
                    assert [e["uuid"] for e in payload if e["type"] == "user_message"] == [
                        f"user_message-recent-{i}" for i in range(10)
                    ]
                row = {
                    "prefix_turns": prefix_turns,
                    "internal_tail_mib": internal_mib,
                    "mode": mode,
                    "journal_bytes": path.stat().st_size,
                    "response_bytes": len(encoded),
                    "returned_events": len(payload),
                    "repeats": args.repeats,
                    "samples_ms": elapsed,
                    "median_ms": statistics.median(elapsed),
                    "p95_ms": sorted(elapsed)[int((args.repeats - 1) * 0.95)],
                    "max_ms": max(elapsed),
                }
                rows.append(row)
                print(json.dumps({k: v for k, v in row.items() if k != "samples_ms"}), flush=True)
    unchanged = identity == sources()
    report = {
        "schema": 1,
        "kind": "offline_first_page_growth",
        "rows": rows,
        "base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": identity,
        "source_unchanged": unchanged,
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "real_llm_calls": 0,
        "limits": [
            "storage plus JSON encoding only; no HTTP or desktop rendering",
            "fresh store for each read; OS filesystem cache uncontrolled",
            "unindexed legacy tail traversal is not a strict constant-I/O proof",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    if not unchanged:
        raise RuntimeError("measured sources changed during probe")


if __name__ == "__main__":
    main()
