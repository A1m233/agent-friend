"""Cross-platform affected checks with explicit full and bounded stage entrypoints."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from impact import STAGES

ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the agent-friend merge gate, optionally as bounded stages.",
    )
    parser.add_argument(
        "--stage",
        choices=(*STAGES, "affected", "all"),
        help="Run one stage, all stages, or affected stages (default).",
    )
    parser.add_argument(
        "--allow-long-running",
        action="store_true",
        help="Allow multiple stages in one Codex command despite the timeout risk.",
    )
    parser.add_argument(
        "--plan", action="store_true", help="Print the verification plan without running checks."
    )
    parser.add_argument(
        "--affected-platform",
        action="append",
        choices=("macos", "windows"),
        help="Platform whose native behavior changes; repeat for both.",
    )
    parser.add_argument(
        "--base", help="Trusted Git baseline for affected checks (default: origin/main)."
    )
    return parser


def resolve_stages(
    requested: str | None,
    *,
    is_codex: bool,
    allow_long_running: bool,
    affected_stages: Sequence[str] = (),
) -> tuple[str, ...]:
    stage = requested or "affected"
    stages = (
        tuple(affected_stages) if stage == "affected" else STAGES if stage == "all" else (stage,)
    )
    if is_codex and len(stages) > 1 and not allow_long_running:
        raise ValueError("codex_requires_bounded_stages")
    return stages


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
    plan = None
    if args.plan or args.stage in {None, "affected"}:
        from platform_plan import plan_for_checkout

        try:
            plan = plan_for_checkout(ROOT, args.base or "origin/main", args.affected_platform or [])
        except ValueError as error:
            parser.error(str(error))
    if args.plan:
        if args.stage or pytest_args or args.allow_long_running:
            parser.error("--plan cannot run a stage or accept test options")
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    if args.stage not in {None, "affected"} and (args.affected_platform or args.base):
        parser.error("--affected-platform and --base require affected checks or --plan")

    selected = tuple(plan["localStages"]) if plan is not None else STAGES
    if plan is not None:
        print(
            f"===> affected checks against {plan['base']} ({plan['baseCommit']}): "
            f"{', '.join(selected) or 'none'}",
            flush=True,
        )

    try:
        stages = resolve_stages(
            args.stage,
            is_codex=bool(os.getenv("CODEX_THREAD_ID")),
            allow_long_running=args.allow_long_running,
            affected_stages=selected,
        )
    except ValueError:
        print(
            "[error] Run the selected stages as separate commands under Codex:\n"
            + "\n".join(f"  ./scripts/check/run.sh --stage {stage}" for stage in selected)
            + "\nWindows: replace run.sh with run.ps1. All selected stages must pass.",
            file=sys.stderr,
        )
        return 2

    if pytest_args and "backend" not in stages:
        parser.error("pytest arguments require a selected backend stage")
    if not stages:
        print("===> no affected local stages; no tests executed (content/adapter checks may apply)")
        return 0

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
