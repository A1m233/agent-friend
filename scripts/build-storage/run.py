"""Lightweight build storage CLI; never initializes a project environment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))

from build_storage import Storage
from build_storage.config import CATEGORIES
from build_storage.files import GIB
from build_storage.policy import collect, plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "status",
            "inventory",
            "configure",
            "gc",
            "pin",
            "unpin",
            "adopt",
            "reconcile",
            "benchmark",
        ),
    )
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--category", action="append", choices=CATEGORIES)
    parser.add_argument("--build-root", type=Path)
    parser.add_argument("--budget-gib", type=float)
    parser.add_argument("--low-water-gib", type=float)
    parser.add_argument("--automatic", choices=("on", "off"))
    parser.add_argument("--object-id")
    parser.add_argument("--reason")
    parser.add_argument("--path", type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--confirm-stopped", action="store_true")
    args = parser.parse_args()
    if args.command == "benchmark":
        from benchmark import run

        print(json.dumps(run(), ensure_ascii=False, indent=2))
        return 0
    storage = Storage(Path(__file__).resolve().parents[2])
    if args.apply and args.command not in {"gc", "adopt"}:
        parser.error("--apply is only valid for gc")
    if args.command == "reconcile":
        if not args.operation_id or not args.confirm_stopped or not args.reason:
            parser.error("reconcile requires --operation-id, --confirm-stopped and --reason")
        result = storage.reconcile(args.operation_id, reason=args.reason)
    elif args.command == "adopt":
        from build_storage.execution import adopt_legacy_package

        if args.path is None:
            parser.error("adopt requires --path")
        result = adopt_legacy_package(storage, args.path, apply=args.apply)
    elif args.command == "configure":
        changes = {}
        if args.build_root is not None:
            changes["buildRoot"] = str(args.build_root)
        if args.budget_gib is not None:
            changes["budgetBytes"] = args.budget_gib * GIB
        if args.low_water_gib is not None:
            changes["lowWaterBytes"] = args.low_water_gib * GIB
        if args.automatic == "on":
            if not args.category:
                parser.error("--automatic on requires explicit --category scope")
            changes["automaticCategories"] = args.category
        elif args.automatic == "off":
            changes["automaticCategories"] = []
        result = storage.configure(changes)
    elif args.command in {"pin", "unpin"}:
        if not args.object_id or (args.command == "pin" and not args.reason):
            parser.error("pin/unpin requires --object-id; pin also requires --reason")
        storage.pin(args.object_id, args.reason if args.command == "pin" else None)
        result = {"schemaVersion": 1, "objectId": args.object_id, "updated": True}
    elif args.command == "gc":
        report = plan(storage, scan=args.scan or args.apply, categories=args.category)
        result = collect(storage, report) if args.apply else report
    else:
        result = storage.inventory(scan=args.scan, categories=args.category)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("failed") or result.get("complete") is False else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
