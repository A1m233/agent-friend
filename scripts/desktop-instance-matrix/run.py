#!/usr/bin/env python3
"""Run deterministic desktop instance-governance contract checks."""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print(f"===> {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--windows-real",
        action="store_true",
        help="Require the separately governed installed-executable matrix (not yet automated here).",
    )
    args = parser.parse_args()

    run(
        ["cargo", "test", "--lib", "instance", "--", "--nocapture"],
        cwd=ROOT / "frontend" / "src-tauri",
    )
    run(
        [
            "uv",
            "run",
            "pytest",
            "scripts/package/test_install_contract.py",
            "tools/tests/test_dev_runtime_guard.py",
            "scripts/check/test_diagnostic_bundle_contract.py",
            "-q",
        ]
    )

    if args.windows_real:
        if platform.system() != "Windows":
            raise RuntimeError("--windows-real requires Windows")
        raise RuntimeError(
            "windows-real requires exact production and installer-smoke artifacts; "
            "build/run them through the package-release workflow before claiming AC-14"
        )

    print("===> source desktop instance matrix passed")
    print("     Windows installed-executable matrix: not requested / not verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"  [ERROR] command failed with exit code {error.returncode}", file=sys.stderr)
        raise SystemExit(error.returncode) from error
    except RuntimeError as error:
        print(f"  [ERROR] {error}", file=sys.stderr)
        raise SystemExit(1) from error
