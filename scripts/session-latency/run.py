"""Session diagnostics; explicit config probe mutates then restores settings."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "prepare":
        return subprocess.call(
            [sys.executable, str(Path(__file__).with_name("prepare_probe.py")), *sys.argv[2:]],
            cwd=ROOT,
        )
    if len(sys.argv) > 1 and sys.argv[1] == "long-runtime":
        return subprocess.call(
            ["node", str(Path(__file__).with_name("long_runtime_probe.mjs")), *sys.argv[2:]],
            cwd=ROOT,
        )
    if len(sys.argv) > 1 and sys.argv[1] == "isolated":
        return subprocess.call(
            [sys.executable, str(Path(__file__).with_name("isolated_runtime.py")), *sys.argv[2:]],
            cwd=ROOT,
        )
    if len(sys.argv) > 1 and sys.argv[1] == "lock-compare":
        return subprocess.call(
            [sys.executable, str(Path(__file__).with_name("lock_comparison.py")), *sys.argv[2:]],
            cwd=ROOT,
        )
    if len(sys.argv) > 1 and sys.argv[1] in {"restore", "locks"}:
        return subprocess.call(
            [
                sys.executable,
                str(
                    Path(__file__).with_name(
                        "restore_probe.py" if sys.argv[1] == "restore" else "lock_probe.py"
                    )
                ),
                *sys.argv[2:],
            ],
            cwd=ROOT,
        )
    if len(sys.argv) > 1 and sys.argv[1] in {"config", "scroll", "real", "paging"}:
        return subprocess.call(
            ["node", str(Path(__file__).with_name(f"{sys.argv[1]}_probe.mjs")), *sys.argv[2:]],
            cwd=ROOT,
        )
    return subprocess.call(
        ["node", str(Path(__file__).with_name("probe.mjs")), *sys.argv[1:]], cwd=ROOT
    )


if __name__ == "__main__":
    raise SystemExit(main())
