"""Bounded, offline child processes used only by the M0 qualification probe."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def write_marker(path: Path) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    pending.replace(path)


def main() -> None:
    mode, directory = sys.argv[1:]
    root = Path(directory)
    if mode == "root-with-child":
        subprocess.Popen(
            [sys.executable, __file__, "child", str(root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        deadline = time.monotonic() + 10
        while not (root / "child.json").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not (root / "child.json").exists():
            raise RuntimeError("child_did_not_start")
        return
    if mode not in {"quiet", "child"}:
        raise ValueError("unknown fixture mode")
    write_marker(root / f"{mode}.json")
    deadline = time.monotonic() + 30  # Backup bound even if the controller is interrupted.
    while time.monotonic() < deadline and not (root / "stop").exists():
        time.sleep(0.02)


if __name__ == "__main__":
    main()
