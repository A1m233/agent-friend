"""Bounded, read-only discovery. Finding a directory never grants ownership."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from .config import default_build_root
from .files import safe_directory, tree_size
from .store import Storage


def discover(
    storage: Storage, *, scan: bool = False, roots: list[Path] | None = None
) -> dict[str, Any]:
    inventory = storage.inventory(scan=scan)
    managed = {Path(r["path"]): r for r in inventory["objects"] if "path" in r}
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "agent-friend"
    candidates = [
        default_build_root(),
        local / "packaging",
        local / "cargo-check",
        local / "dev-cache",
    ]
    if storage.config["buildRoot"]:
        candidates.append(Path(storage.config["buildRoot"]))
    candidates.extend(roots or [])
    for target in managed:
        candidates.append(target)
        if len(target.parents) >= 4 and target.parents[2].name == "work":
            candidates.append(target.parents[3])
    coverage, errors, valid = [], [], []
    for candidate in sorted(set(candidates), key=str):
        try:
            target = safe_directory(candidate, storage.repo, storage.control)
            if target.exists():
                valid.append(target)
            coverage.append({"path": str(target), "exists": target.exists()})
        except (OSError, ValueError) as error:
            errors.append({"path": str(candidate), "error": str(error)})
    # Outer roots cover inner roots exactly once.
    outer = [p for p in valid if not any(p != q and p.is_relative_to(q) for q in valid)]
    rows: list[dict[str, Any]] = []
    pending = [(p, 0) for p in outer]
    visited = 0
    try:
        while pending:
            target, depth = pending.pop()
            visited += 1
            if visited > 5000:
                errors.append({"path": str(target), "error": "discovery limit reached"})
                break
            if target in managed:
                continue
            try:
                safe_directory(target, storage.repo, storage.control)
                contains_managed = any(p.is_relative_to(target) for p in managed)
                # Stop before walking compiler internals. Enumerate only directory-layout levels.
                workspace = (target / "workspace-state.json").is_file() or (
                    target.parent.parent.name == "work" and (target / "build-cache").is_dir()
                )
                package_leaf = (
                    target.name == "build-cache"
                    or (target / "config/build-identity.json").is_file()
                    or target.name
                    in {
                        "debug",
                        "release",
                        "deps",
                        "incremental",
                        "pyinstaller",
                        "runtime-stage",
                        "payload-stage",
                        "cache",
                        "uv",
                    }
                )
                expand = contains_managed or workspace or (depth < 3 and not package_leaf)
                children = sorted(target.iterdir(), key=str) if expand else []
                dirs = [p for p in children if p.is_dir() or p.is_symlink() or p.is_junction()]
                if expand and dirs:
                    pending.extend((p, depth + 1) for p in dirs)
                    files = [p for p in children if p not in dirs]
                    if files:
                        rows.append(
                            {
                                "path": str(target),
                                "scope": "direct-files",
                                "state": "unmanaged",
                                "bytes": sum(p.stat().st_size for p in files) if scan else None,
                                "sizeStatus": "measured" if scan else "unknown",
                                "adoptable": False,
                                "reason": "layout-metadata",
                            }
                        )
                    continue
                row = {
                    "path": str(target),
                    "scope": "tree",
                    "state": "unmanaged",
                    "bytes": None,
                    "sizeStatus": "unknown",
                    "adoptable": False,
                    "reason": "ownership-unproven",
                }
                if target.parent.parent.parent.name == "work":
                    from .execution import adopt_legacy_package

                    try:
                        row["adoption"] = adopt_legacy_package(
                            storage, target, allow_cargo_evidence=True
                        )
                        row.update(adoptable=True, reason="explicit-adoption-required")
                    except (OSError, ValueError, KeyError) as error:
                        row["reason"] = str(error)
                if scan:
                    print(f"scan unmanaged: {target}", file=sys.stderr, flush=True)
                    row.update(
                        bytes=tree_size(target), sizeStatus="measured", measuredAt=time.time()
                    )
                rows.append(row)
            except (OSError, ValueError) as error:
                errors.append({"path": str(target), "error": str(error)})
    except KeyboardInterrupt:
        errors.append({"error": "scan cancelled; partial results"})
    all_rows = [*inventory["objects"], *rows]
    return {
        **inventory,
        "coverage": coverage,
        "unmanaged": rows,
        "discoveryErrors": errors,
        "complete": inventory["complete"] and not errors,
        "summary": {
            "knownLogicalBytes": sum(r.get("bytes") or 0 for r in all_rows),
            "unmanagedLogicalBytes": sum(r.get("bytes") or 0 for r in rows),
            "unknownOrStaleCount": sum(r.get("sizeStatus") != "measured" for r in all_rows),
            "observedAt": time.time(),
            "scope": "listed-roots-only",
        },
    }
