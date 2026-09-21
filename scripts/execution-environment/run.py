"""Prepare product-owned Windows execution dependencies outside the checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import portalocker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "package"))

from vendor import managed_python, native_tools  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify", "smoke"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Packaged execution dependencies currently support Windows x64 only")
    identity = hashlib.sha256(
        managed_python.LOCK_PATH.read_bytes() + native_tools.LOCK_PATH.read_bytes()
    ).hexdigest()
    cache = (
        Path(os.environ["LOCALAPPDATA"]) / "agent-friend" / "dev-cache" / "execution-environment"
    )
    destination = cache / identity
    cache.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(cache / f"{identity}.lock"), timeout=300):
        if args.action == "prepare":
            if not (destination / "python").exists():
                managed_python.stage_for_package(
                    ROOT,
                    destination / "python",
                    cache_base=cache,
                    offline=args.offline,
                )
            native_tools.stage_for_package(
                destination / "native",
                cache_base=cache,
                offline=args.offline,
            )
        managed_python.verify_prepared(destination / "python")
        native_tools.verify(destination / "native")
    if args.action == "smoke":
        import runtime_entry

        os.environ["AGENT_FRIEND_EXECUTION_ENVIRONMENT_ROOT"] = str(destination)
        print(json.dumps(runtime_entry._smoke_managed_execution_environment()))
    else:
        print(destination)


if __name__ == "__main__":
    main()
