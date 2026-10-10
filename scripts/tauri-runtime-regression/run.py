"""Verify the pinned runtime patch and its owner-thread lifetime contract."""

from __future__ import annotations

import argparse
import json
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

MANIFEST = ROOT / "frontend/src-tauri/Cargo.toml"
VENDOR = ROOT / "frontend/src-tauri/vendor/tauri-runtime-wry/Cargo.toml"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("check", "unit", "stress", "desktop-stress"), nargs="?", default="unit"
    )
    parser.add_argument("--target-dir", type=Path)
    parser.add_argument(
        "--release", action="store_true", help="Use release for the isolated stress process"
    )
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--candidate-build-id")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--duration-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.release and args.mode != "stress":
        parser.error("--release is only used by stress")
    if args.mode == "desktop-stress":
        if not args.installer or not args.candidate_build_id or not args.artifacts:
            parser.error(
                "desktop-stress requires --installer, --candidate-build-id and --artifacts"
            )
        from desktop_stress import run

        raise SystemExit(
            0
            if run(args.installer, args.candidate_build_id, args.artifacts, args.duration_seconds)
            else 1
        )
    base = ["cargo", "--manifest-path", str(MANIFEST)]
    metadata = subprocess.run(
        [base[0], "metadata", *base[1:], "--format-version", "1", "--locked"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if metadata.returncode:
        raise SystemExit(metadata.stderr)
    packages = json.loads(metadata.stdout)["packages"]
    runtime = [package for package in packages if package["name"] == "tauri-runtime-wry"]
    if (
        len(runtime) != 1
        or runtime[0]["version"] != "2.11.2"
        or Path(runtime[0]["manifest_path"]).resolve() != VENDOR.resolve()
    ):
        raise SystemExit("Cargo did not resolve the pinned local runtime patch")
    print("[ok] Cargo resolves local tauri-runtime-wry 2.11.2", flush=True)
    if args.mode == "check":
        return
    storage = Storage(ROOT)
    target = storage.target(
        "runtime-regression",
        args.target_dir
        or (Path(os.environ["CARGO_TARGET_DIR"]) if os.environ.get("CARGO_TARGET_DIR") else None),
    )
    admit(storage, [("runtime-regression", target, 12 * GIB)])
    with (
        reserve(storage, [("runtime-regression", target, 12 * GIB)]),
        use_target(storage, target, "runtime-regression") as record,
    ):
        common = [*base[1:], "-p", "tauri-runtime-wry", "--locked", "--target-dir", str(target)]
        if args.mode == "unit":
            for features in ([], ["--features", "tauri-runtime-wry/tracing"]):
                subprocess.run(["cargo", "test", *common, "--lib", *features], cwd=ROOT, check=True)
        else:
            if os.name != "nt":
                raise SystemExit(
                    "Native runtime stress is Windows-only; no desktop runtime was started"
                )
            command = ["cargo", "build", *common, "--example", "owner_thread_regression"]
            if args.release:
                command.append("--release")
            subprocess.run(command, cwd=ROOT, check=True)
            binary = (
                target
                / ("release" if args.release else "debug")
                / "examples/owner_thread_regression.exe"
            )
            # Every native loop owns a fresh process; no application user data/LLM.
            for scenario in (
                "drop-before-run",
                "iteration",
                "run-return",
                "close-with-pending",
                "clone-stress",
            ):
                print(f"[run] isolated Windows runtime: {scenario}", flush=True)
                subprocess.run([str(binary), scenario], cwd=ROOT, check=True, timeout=120)

        if record:
            storage.archive(record, "passed", [])
    maintain(storage)


if __name__ == "__main__":
    main()
