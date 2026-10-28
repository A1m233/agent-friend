"""Protect one foreground build command's target for its complete lifetime."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))
from build_storage.execution import run_cargo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("category", choices=("dev", "rust-check", "runtime-regression"))
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--target-dir", type=Path)
    parser.add_argument("--runtime-owner-pid", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    return run_cargo(
        Path(__file__).resolve().parents[2],
        args.category,
        command,
        target=args.target_dir,
        cwd=args.cwd,
        runtime_owner_pid=args.runtime_owner_pid,
    )


if __name__ == "__main__":
    raise SystemExit(main())
