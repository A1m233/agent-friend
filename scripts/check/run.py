"""Cross-platform full-gate orchestrator with bounded stages for coding agents."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGES = ("backend", "frontend", "rust")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the agent-friend merge gate, optionally as bounded stages.",
    )
    parser.add_argument(
        "--stage",
        choices=(*STAGES, "all"),
        help="Run one bounded stage; ordinary terminals default to all.",
    )
    parser.add_argument(
        "--allow-long-running",
        action="store_true",
        help="Allow --stage all under Codex despite the command timeout risk.",
    )
    return parser


def resolve_stages(
    requested: str | None,
    *,
    is_codex: bool,
    allow_long_running: bool,
) -> tuple[str, ...]:
    stage = requested or "all"
    if is_codex and stage == "all" and not allow_long_running:
        raise ValueError("codex_requires_bounded_stages")
    return STAGES if stage == "all" else (stage,)


def _script(path_without_suffix: str) -> list[str]:
    base = ROOT / "scripts" / path_without_suffix
    if os.name == "nt":
        return [
            "powershell",
            "-NoProfile",
            "-File",
            str(base.with_suffix(".ps1")),
        ]
    return [str(base.with_suffix(".sh"))]


def _run(label: str, command: Sequence[str]) -> None:
    started = time.monotonic()
    print(f"===> {label}", flush=True)
    completed = subprocess.run(command, cwd=ROOT, check=False)
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        print(f"  [failed] {label} ({elapsed:.1f}s)", flush=True)
        raise SystemExit(completed.returncode)
    print(f"  [ok] {label} ({elapsed:.1f}s)", flush=True)


def _run_backend(pytest_args: Sequence[str]) -> None:
    _run("backend 1/3 lint", _script("lint/run"))
    _run("backend 2/3 typecheck", _script("typecheck/run"))
    _run("backend 3/3 pytest", [*_script("test/run"), *pytest_args])


def _run_frontend() -> None:
    node_modules = ROOT / "frontend" / "node_modules"
    if not node_modules.is_dir():
        print(
            "[error] frontend dependencies are not installed; run "
            "./scripts/frontend/install.sh (macOS/Linux) or "
            r".\scripts\frontend\install.ps1 (Windows), then rerun the gate",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
    _run("frontend 1/3 lint", _script("frontend/lint"))
    _run("frontend 2/3 test", _script("frontend/test"))
    _run("frontend 3/3 production build", _script("frontend/build"))


def _run_rust() -> None:
    _run("rust Tauri Core test + release check", _script("rust-check/run"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args, pytest_args = parser.parse_known_args(argv)
    if args.stage in {"frontend", "rust"} and pytest_args:
        parser.error("pytest arguments are only accepted by --stage backend or all")

    try:
        stages = resolve_stages(
            args.stage,
            is_codex=bool(os.getenv("CODEX_THREAD_ID")),
            allow_long_running=args.allow_long_running,
        )
    except ValueError:
        print(
            "[error] Codex commands have a bounded runtime; the full sequential gate can "
            "exceed it and orphan build children. Run these as separate commands:\n"
            "  ./scripts/check/run.sh --stage backend\n"
            "  ./scripts/check/run.sh --stage frontend\n"
            "  ./scripts/check/run.sh --stage rust\n"
            "Windows: replace run.sh with run.ps1. Each stage must pass.",
            file=sys.stderr,
        )
        return 2

    started = time.monotonic()
    for stage in stages:
        if stage == "backend":
            _run_backend(pytest_args)
        elif stage == "frontend":
            _run_frontend()
        else:
            _run_rust()
    print(f"===> check stages passed: {', '.join(stages)} ({time.monotonic() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
