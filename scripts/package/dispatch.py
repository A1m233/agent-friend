"""Route management before dependency setup; preflight actual package writes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = {"workspace-status", "workspace-gc", "workspace-adopt", "plan"}
BUILD = {"runtime", "runtime-verify", "installer", "all", "installer-smoke-build"}


def main() -> int:
    args = sys.argv[1:]
    step = args[0] if args else "all"
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
    return subprocess.call(
        [
            "uv",
            "run",
            "--group",
            "package",
            "--group",
            "memory-retrieval-benchmark",
            "python",
            str(Path(__file__).with_name("run.py")),
            *args,
        ],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
