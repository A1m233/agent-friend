"""Route management before dependency setup; preflight actual package writes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from source_checkout import package_checkout
from windows_host import maybe_relay

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = {"workspace-status", "workspace-gc", "workspace-adopt", "plan"}
MANAGEMENT.update(
    {
        "artifact-status",
        "artifact-configure",
        "artifact-adopt",
        "artifact-pin",
        "artifact-unpin",
        "artifact-gc",
        "artifact-retry",
    }
)
BUILD = {"runtime", "runtime-verify", "installer", "all", "installer-smoke-build"}


def main() -> int:
    args = sys.argv[1:]
    step = args[0] if args else "all"
    relayed = maybe_relay(args)
    if relayed is not None:
        return relayed
    if step in MANAGEMENT:
        return subprocess.call(
            [sys.executable, "-B", str(Path(__file__).with_name("manage.py")), *args], cwd=ROOT
        )
    if step in BUILD and "--help" not in args:
        preflight_args = []
        for i, arg in enumerate(args):
            if arg in {"--state-dir", "--releases-dir"}:
                if i + 1 >= len(args):
                    raise ValueError(f"missing value for {arg}")
                preflight_args.extend([arg, args[i + 1]])
            elif arg.startswith(("--state-dir=", "--releases-dir=")):
                preflight_args.append(arg)
        result = subprocess.call(
            [
                sys.executable,
                "-B",
                str(Path(__file__).with_name("manage.py")),
                "storage-preflight",
                *preflight_args,
            ],
            cwd=ROOT,
        )
        if result:
            return result
    smoke = step in {"installer-smoke-build", "installer-smoke", "installer-smoke-verify"}
    if "--help" not in args and (smoke or (step in BUILD and "--local-test" in args)):
        with package_checkout(ROOT) as checkout:
            if step in {"all", "installer", "installer-smoke-build"}:
                pnpm = shutil.which("pnpm")
                if pnpm is None:
                    raise RuntimeError("pnpm is required for installer builds")
                install = [pnpm, "install", "--frozen-lockfile"]
                if "--offline" in args:
                    install.append("--offline")
                subprocess.run(install, cwd=checkout / "frontend", check=True)
            return _run_package(checkout, args)
    return _run_package(ROOT, args)


def _run_package(checkout: Path, args: list[str]) -> int:
    env = os.environ.copy()
    if checkout != ROOT:
        env["UV_PROJECT_ENVIRONMENT"] = str(checkout / ".venv")
        env["AGENT_FRIEND_PACKAGE_OWNER_WORKTREE"] = str(ROOT)
    else:
        env.pop("AGENT_FRIEND_PACKAGE_OWNER_WORKTREE", None)
    return subprocess.call(
        [
            "uv",
            "run",
            "--project",
            str(checkout),
            "--group",
            "package",
            "--group",
            "memory-retrieval-benchmark",
            "python",
            str(checkout / "scripts" / "package" / "run.py"),
            *args,
        ],
        cwd=ROOT,
        env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
