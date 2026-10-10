"""Run both Rust check stages under one governed target lifetime."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))
from build_storage import Storage
from build_storage.execution import admit, use_target
from build_storage.files import GIB
from build_storage.policy import maintain
from build_storage.reservations import reserve

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    storage = Storage(ROOT)
    target = storage.target(
        "rust-check",
        Path(os.environ["CARGO_TARGET_DIR"]) if os.environ.get("CARGO_TARGET_DIR") else None,
    )
    admit(storage, [("rust-check", target, 12 * GIB)])
    args = sys.argv[1:]
    if any(a == "--target-dir" or a.startswith("--target-dir=") for a in args):
        raise ValueError("set CARGO_TARGET_DIR to apply an explicit target to both Rust stages")
    env = {**os.environ, "CARGO_TARGET_DIR": str(target)}
    with (
        reserve(storage, [("rust-check", target, 12 * GIB)]),
        use_target(storage, target, "rust-check") as record,
    ):
        for command in (
            ["cargo", "fmt", "--check", "--manifest-path", "frontend/src-tauri/Cargo.toml"],
            ["cargo", "test", "--manifest-path", "frontend/src-tauri/Cargo.toml", *args],
            ["cargo", "check", "--release", "--manifest-path", "frontend/src-tauri/Cargo.toml"],
        ):
            subprocess.run(command, cwd=ROOT, env=env, check=True)
        if record:
            storage.archive(record, "passed", [])
    maintain(storage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
