"""Same-file controlled read stall against the actual Git baseline and candidate."""

from __future__ import annotations

import argparse
import builtins
import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from agent.paths import sessions_dir

from agent import Event, JsonlSessionStore

ROOT = Path(__file__).resolve().parents[2]


class PausedRead:
    def __init__(self, handle: object, entered: threading.Event, release: threading.Event):
        self.handle, self.entered, self.release = handle, entered, release

    def __getattr__(self, name: str) -> object:
        return getattr(self.handle, name)

    def __enter__(self) -> PausedRead:
        return self

    def __exit__(self, *args: object) -> None:
        self.handle.close()

    def read(self, size: int = -1) -> bytes:
        if not self.entered.is_set():
            self.entered.set()
            if not self.release.wait(20):
                raise TimeoutError("read stall controller did not release")
        return self.handle.read(size)


def compare_variant(store_type: type, source: Path, session_id: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="agent-friend-lock-comparison-") as directory:
        target = Path(directory) / source.name
        shutil.copyfile(source, target)
        store = store_type(Path(directory))
        entered, release = threading.Event(), threading.Event()
        marker = "lock-comparison-result"
        store.append_event(
            session_id,
            Event(
                type="tool_call_request",
                uuid="lock-comparison-request",
                ts=datetime.now(UTC),
                payload={
                    "tool_call_id": marker,
                    "event_tool_call_id": marker,
                    "tool_name": "calculate",
                    "args": {"expression": "17*23"},
                },
            ),
        )
        original_open, original_fdopen = builtins.open, os.fdopen

        def wrap(handle: object) -> object:
            return (
                PausedRead(handle, entered, release)
                if threading.current_thread().name.startswith("paused-reader")
                else handle
            )

        def opened(*args: object, **kwargs: object) -> object:
            return wrap(original_open(*args, **kwargs))

        def fdopened(*args: object, **kwargs: object) -> object:
            return wrap(original_fdopen(*args, **kwargs))

        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="paused-reader") as reader_pool,
            patch("builtins.open", opened),
            patch("os.fdopen", fdopened),
        ):
            reader = reader_pool.submit(store.load, session_id)
            try:
                if not entered.wait(10):
                    raise RuntimeError("actual reader did not reach binary read")
                started = time.perf_counter()
                try:
                    store.append_event(
                        session_id,
                        Event(
                            type="tool_call_result",
                            uuid=marker,
                            ts=datetime.now(UTC),
                            payload={
                                "tool_call_id": marker,
                                "event_tool_call_id": marker,
                                "tool_name": "calculate",
                                "result": "391",
                                "is_error": False,
                            },
                        ),
                    )
                    write_error = None
                except Exception as error:
                    write_error = type(error).__name__
                write_ms = (time.perf_counter() - started) * 1000
                write_finished_while_read_paused = not release.is_set()
            finally:
                release.set()
            loaded = reader.result(timeout=45)
        reader_saw_new_result = any(event.uuid == marker for event in loaded.events)
        del loaded
        persisted = store.load(session_id)
        results = [event for event in persisted.events if event.uuid == marker]
        return {
            "writeError": write_error,
            "writeMs": write_ms,
            "writeFinishedWhileReadPaused": write_finished_while_read_paused,
            "readerSawNewResult": reader_saw_new_result,
            "persistedResults": len(results),
            "correctResult": len(results) == 1 and results[0].payload["result"] == "391",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    source = JsonlSessionStore(sessions_dir())._existing_path(args.session_id)
    if source is None:
        parser.error("session not found")
    before = source.stat()
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{args.baseline_ref}^{{commit}}"],
        cwd=ROOT,
        text=True,
    ).strip()
    code = subprocess.check_output(
        ["git", "show", f"{revision}:agent/src/agent/sessions/store.py"], cwd=ROOT
    )
    baseline = types.ModuleType("agent.sessions._lock_comparison_baseline")
    baseline.__package__ = "agent.sessions"
    sys.modules[baseline.__name__] = baseline
    exec(compile(code, f"git:{revision}:store.py", "exec"), baseline.__dict__)
    rows = {}
    for name, store_type in (
        ("baseline", baseline.JsonlSessionStore),
        ("candidate", JsonlSessionStore),
    ):
        rows[name] = compare_variant(store_type, source, args.session_id)
        print(json.dumps({name: rows[name]}), flush=True)
        gc.collect()
    unchanged = (source.stat().st_size, source.stat().st_mtime_ns) == (
        before.st_size,
        before.st_mtime_ns,
    )
    passed = (
        unchanged
        and rows["baseline"]["writeError"] == "SessionPersistError"
        and rows["baseline"]["persistedResults"] == 0
        and rows["candidate"]["writeError"] is None
        and rows["candidate"]["correctResult"]
        and not rows["candidate"]["readerSawNewResult"]
    )
    report = {
        "baselineRef": revision,
        "baselineStoreSha256": hashlib.sha256(code).hexdigest(),
        "candidateStoreSha256": hashlib.sha256(
            (ROOT / "agent/src/agent/sessions/store.py").read_bytes()
        ).hexdigest(),
        "sourceBytes": before.st_size,
        "sourceUnchanged": unchanged,
        "rows": rows,
        "passed": passed,
        "limits": "Controlled read stall, not natural incident reproduction. Original 10s writer timeout; separate temporary copies; actual store code and file locks. No model calls. Does not identify historical caller threads.",
    }
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
