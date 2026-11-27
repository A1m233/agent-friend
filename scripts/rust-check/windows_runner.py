"""Embed Common Controls v6 in owned Cargo test executables before Windows loads them.

Tauri embeds this dependency in the product binary, but Rust lib test harnesses
do not run that resource step. rfd statically imports TaskDialogIndirect, so the
test process otherwise exits with STATUS_ENTRYPOINT_NOT_FOUND before main.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    executable = Path(sys.argv[1]).resolve()
    target = Path(os.environ["CARGO_TARGET_DIR"]).resolve()
    if not executable.is_relative_to(target) or executable.suffix.lower() != ".exe":
        raise ValueError("test runner only accepts executables in the governed Cargo target")
    kits = (
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Windows Kits/10/bin"
    )
    tools = sorted(
        kits.glob("*/x64/mt.exe"),
        key=lambda path: tuple(int(p) for p in path.parent.parent.name.split(".")),
    )
    if not tools:
        raise RuntimeError("Windows SDK manifest tool (mt.exe) is required for native tests")
    subprocess.run(
        [
            str(tools[-1]),
            "-nologo",
            "-manifest",
            str(Path(__file__).with_name("windows-test.manifest")),
            f"-outputresource:{executable};#1",
        ],
        check=True,
    )
    return subprocess.run([str(executable), *sys.argv[2:]], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
