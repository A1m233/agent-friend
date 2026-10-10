"""Read-only package management without loading the build environment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))

from build_storage import Storage
from build_storage.execution import adopt_legacy_package
from build_storage.locks import lock
from build_storage.policy import collect, plan
from paths import resolve_package_roots
from storage_governance import emit_plan, package_guard, preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "step",
        choices=(
            "workspace-status",
            "workspace-gc",
            "workspace-adopt",
            "plan",
            "storage-preflight",
        ),
    )
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--releases-dir", type=Path)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--path", type=Path)
    parser.add_argument(
        "--purpose",
        default="all",
        choices=("runtime-verify", "runtime", "installer", "all", "installer-smoke-build"),
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    roots = resolve_package_roots(root, state_dir=args.state_dir, releases_dir=args.releases_dir)
    storage = Storage(root)
    if args.apply and args.step not in {"workspace-gc", "workspace-adopt"}:
        parser.error("--apply requires workspace-gc or workspace-adopt")
    if args.step == "plan":
        emit_plan(roots, purpose=args.purpose)
        return 0
    if args.step == "storage-preflight":
        with package_guard(roots, lock):
            result = preflight(roots)
    elif args.step == "workspace-adopt":
        if args.path is None:
            parser.error("workspace-adopt requires --path")
        with package_guard(roots, lock):
            result = adopt_legacy_package(storage, args.path, apply=args.apply)
    elif args.step == "workspace-gc":
        if args.apply:
            with package_guard(roots, lock):
                result = collect(
                    storage, plan(storage, scan=True, categories=["package-cache", "package-build"])
                )
        else:
            result = plan(storage, scan=args.scan, categories=["package-cache", "package-build"])
    else:
        result = storage.inventory(scan=args.scan, categories=["package-cache", "package-build"])
        # A bounded directory listing identifies unadopted historical workspaces without measuring files.
        legacy_root = roots.state_root / "work" / roots.repo_id
        result["legacyWorkspaceRoot"] = str(legacy_root)
        result["legacyWorkspaces"] = [str(p) for p in sorted(legacy_root.glob("*")) if p.is_dir()]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("failed") or result.get("complete") is False else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
