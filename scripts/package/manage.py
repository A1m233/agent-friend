"""Read-only package management without loading the build environment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))

from build_storage import Storage
from build_storage.discovery import discover
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
            "artifact-status",
            "artifact-configure",
            "artifact-adopt",
            "artifact-pin",
            "artifact-unpin",
            "artifact-gc",
            "artifact-retry",
        ),
    )
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--releases-dir", type=Path)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--path", type=Path)
    parser.add_argument("--cargo-evidence", action="store_true")
    parser.add_argument("--automatic", choices=("on", "off"))
    parser.add_argument("--keep-count", type=int)
    parser.add_argument("--keep-days", type=int)
    parser.add_argument("--reason")
    parser.add_argument(
        "--purpose",
        default="all",
        choices=("runtime-verify", "runtime", "installer", "all", "installer-smoke-build"),
    )
    args = parser.parse_args()
    if args.cargo_evidence and args.step != "workspace-adopt":
        parser.error("--cargo-evidence requires workspace-adopt")
    root = Path(__file__).resolve().parents[2]
    roots = resolve_package_roots(root, state_dir=args.state_dir, releases_dir=args.releases_dir)
    storage = Storage(root)
    if args.apply and args.step not in {
        "workspace-gc",
        "workspace-adopt",
        "artifact-configure",
        "artifact-adopt",
        "artifact-gc",
        "artifact-retry",
    }:
        parser.error("--apply requires workspace-gc or workspace-adopt")
    if args.step.startswith("artifact-"):
        from release_retention import Retention

        retention = Retention(storage, roots.releases_root)
        if (
            args.step in {"artifact-adopt", "artifact-pin", "artifact-unpin", "artifact-retry"}
            and args.path is None
        ):
            parser.error("artifact operation requires --path")
        if args.step == "artifact-configure":
            result = retention.configure(
                enabled=None if args.automatic is None else args.automatic == "on",
                count=args.keep_count,
                days=args.keep_days,
                apply=args.apply,
            )
        elif args.step == "artifact-adopt":
            result = retention.register(args.path, apply=args.apply)
        elif args.step in {"artifact-pin", "artifact-unpin"}:
            if args.step == "artifact-pin" and not (args.reason or "").strip():
                parser.error("artifact-pin requires --reason")
            retention.pin(args.path, args.reason if args.step == "artifact-pin" else None)
            result = {"updated": True}
        elif args.step == "artifact-retry":
            result = (
                retention.retry(args.path)
                if args.apply
                else {"path": str(args.path), "applied": False}
            )
        else:
            result = retention.inventory()
            if args.step == "artifact-gc" and args.apply:
                result = retention.collect(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("failed") or result.get("complete") is False else 0
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
            result = adopt_legacy_package(
                storage, args.path, apply=args.apply, allow_cargo_evidence=args.cargo_evidence
            )
    elif args.step == "workspace-gc":
        if args.apply:
            with package_guard(roots, lock):
                result = collect(
                    storage, plan(storage, scan=True, categories=["package-cache", "package-build"])
                )
        else:
            result = plan(storage, scan=args.scan, categories=["package-cache", "package-build"])
    else:
        result = discover(storage, scan=args.scan, roots=[roots.state_root])
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
