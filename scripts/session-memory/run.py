"""Read-only, offline memory measurements on explicitly supplied session fixtures."""

from __future__ import annotations

import argparse
import ctypes
import dataclasses
import gc
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def memory() -> dict[str, float | None]:
    if os.name != "nt":
        import resource

        scale = 1024**2 if sys.platform == "darwin" else 1024
        return {
            "peak_rss_MiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale,
            "private_MiB": None,
            "peak_private_MiB": None,
        }
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (key, ctypes.c_size_t)
            for key in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
                "PrivateUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
        wintypes.HANDLE(-1), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError()
    return {
        "private_MiB": counters.PrivateUsage / 1024**2,
        "peak_private_MiB": counters.PeakPagefileUsage / 1024**2,
        "working_set_MiB": counters.WorkingSetSize / 1024**2,
    }


def child(directory: Path, session_id: str, variant: str, baseline_ref: str) -> dict[str, Any]:
    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("memory probe forbids network access")

    socket.socket.connect = blocked  # type: ignore[method-assign]
    socket.socket.connect_ex = blocked  # type: ignore[assignment, method-assign]
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    # Match the original isolated baseline: import actual modules without starting
    # unrelated application services or constructing providers.
    for name in ("agent", "llm_providers", "memory", "runtime_config"):
        sys.path.insert(0, str(ROOT / name / "src"))
    for name, path in (
        ("agent", "agent/src/agent"),
        ("agent.sessions", "agent/src/agent/sessions"),
    ):
        module = types.ModuleType(name)
        module.__path__ = [str(ROOT / path)]
        sys.modules[name] = module
    from agent.sessions.rollout import restore_rollout
    from agent.sessions.store import JsonlSessionStore

    store = JsonlSessionStore(directory)
    if variant == "compare":
        revision = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{baseline_ref}^{{commit}}"],
            cwd=ROOT,
            text=True,
        ).strip()
        source = subprocess.check_output(
            ["git", "show", f"{revision}:agent/src/agent/sessions/rollout.py"],
            cwd=ROOT,
        )
        old = types.ModuleType("agent.sessions._memory_baseline_rollout")
        old.__package__ = "agent.sessions"
        sys.modules[old.__name__] = old
        exec(compile(source, f"git:{revision}:rollout.py", "exec"), old.__dict__)

        def digest(result: Any) -> str:
            return hashlib.sha256(
                json.dumps(
                    dataclasses.asdict(result), ensure_ascii=False, sort_keys=True, default=str
                ).encode()
            ).hexdigest()

        previous = old.restore_rollout(store.load(session_id))
        previous_digest, disposition = digest(previous), previous.disposition
        del previous
        gc.collect()
        current = store.load_execution(session_id).restored_rollout
        assert current is not None
        current_digest = digest(current)
        return {
            "variant": variant,
            "baseline_ref": revision,
            "baseline_source_sha256": hashlib.sha256(source).hexdigest(),
            "baseline_result_sha256": previous_digest,
            "candidate_result_sha256": current_digest,
            "equivalent": previous_digest == current_digest,
            "baseline_disposition": disposition,
            "candidate_disposition": current.disposition,
            "limits": "Exact fixtures only: no normalization of generated epoch identities; current JSONL decoder is shared, baseline replay code is pinned.",
        }
    baseline = memory()
    start = time.perf_counter()
    if variant == "execution":
        session = store.load_execution(session_id)
        result = session.restored_rollout
    else:
        session = store.load(session_id)
        result = restore_rollout(session)
    elapsed = time.perf_counter() - start
    assert result is not None
    gc.collect()
    measured = memory()
    row = {
        "variant": variant,
        "python": sys.version.split()[0],
        "seconds": elapsed,
        "imports": baseline,
        "restored": measured,
        "business_events": len(session.active_events),
        "retained_events": len(session.events),
        "current_model_items": len(result.history.items),
        "disposition": result.disposition,
    }
    del session, result, store
    gc.collect()
    row["released"] = memory()
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--variant", choices=("execution", "full", "compare"), default="execution")
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Validate a compact replacement without publishing it",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Publish validated compact data and retain the original backup; requires --compact",
    )
    args = parser.parse_args()
    if args.apply and not args.compact:
        parser.error("--apply requires --compact")
    if args.compact:
        from agent.sessions.maintenance import compact_session
        from agent.sessions.store import JsonlSessionStore

        if args.output is not None and args.output.exists():
            parser.error("output must be a new file")
        result = compact_session(
            JsonlSessionStore(args.directory), args.session_id, apply=args.apply
        )
        print(json.dumps(result))
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as file:
                json.dump(result, file, indent=2)
                file.write("\n")
        return 0
    if args.child:
        print(json.dumps(child(args.directory, args.session_id, args.variant, args.baseline_ref)))
        return 0
    if not 1 <= args.repeat <= 20:
        parser.error("repeat must be between 1 and 20")
    if args.output is None or args.output.exists():
        parser.error("output must be a new file")
    from agent.sessions.store import JsonlSessionStore

    source = JsonlSessionStore(args.directory)._existing_path(args.session_id)
    if source is None:
        parser.error("fixture does not exist")

    def fingerprint() -> str:
        with source.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()

    digest = fingerprint()
    rows = []
    for repeat in range(args.repeat):
        output = subprocess.check_output(
            [
                args.python,
                "-B",
                __file__,
                "--child",
                "--directory",
                str(args.directory.resolve()),
                "--session-id",
                args.session_id,
                "--variant",
                args.variant,
                "--baseline-ref",
                args.baseline_ref,
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        row = json.loads(output)
        row["repeat"] = repeat + 1
        rows.append(row)
        print(json.dumps(row), flush=True)
    if fingerprint() != digest:
        raise RuntimeError("fixture changed during measurement")
    report = {
        "schema_version": 1,
        "source_sha256": digest,
        "rows": rows,
        "limits": "Isolated offline source modules; OS cache not controlled; no running app measurement. full uses current replay implementation, not a historical baseline.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    return 1 if any(row.get("equivalent") is False for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
