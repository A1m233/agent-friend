"""Capacity decisions and volume accounting, shared by preview and execution."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .files import volume
from .locks import Busy
from .store import Storage


def plan(
    storage: Storage, *, scan: bool = False, categories: list[str] | None = None
) -> dict[str, Any]:
    inventory = storage.inventory(scan=scan)
    config = inventory["policy"]
    rows = inventory["objects"]
    known = sum(r.get("bytes") or 0 for r in rows)
    unknown = [r.get("id", r.get("record")) for r in rows if r["sizeStatus"] != "measured"]
    pressure = known > config["budgetBytes"]
    now = time.time()
    candidates = []
    protected = []
    for row in rows:
        if categories is not None and row.get("category") not in categories:
            continue
        reason = None
        if row["state"] in {"running", "unmanaged"}:
            reason = "active-or-unresolved"
        elif row.get("pin"):
            reason = "pinned"
        elif (row.get("retainUntil") or 0) > now:
            reason = "retained"
        elif row["sizeStatus"] != "measured":
            reason = "measurement-required"
        if reason:
            protected.append({"object": row, "reason": reason})
            continue
        expiry = row.get("retainUntil") or row["lastUsedAt"] + config["cacheDays"] * 86400
        expired = expiry <= now
        volume_pressure = row["availableBytes"] < config["lowWaterBytes"]
        if expired or pressure or volume_pressure:
            orphan = not (Path(row["worktree"]) / ".git").exists()
            candidates.append(
                {
                    "object": row,
                    "reason": "expired" if expired else "capacity-pressure",
                    "priority": [not orphan, not expired, row["lastUsedAt"], row["id"]],
                }
            )
    candidates.sort(key=lambda r: r["priority"])
    return {
        "schemaVersion": 1,
        "repoId": storage.repo_id,
        "observedAt": now,
        "knownLogicalBytes": known,
        "unknownObjects": unknown,
        "complete": inventory["complete"],
        "budgetSatisfied": not unknown and known <= config["budgetBytes"],
        "candidates": candidates,
        "protected": protected,
        "policy": config,
    }


def collect(storage: Storage, report: dict[str, Any]) -> dict[str, Any]:
    deleted, skipped, failed = [], [], []
    remaining = report["knownLogicalBytes"]
    for candidate in report["candidates"]:
        record = candidate["object"]
        if storage.config != report["policy"]:
            raise Busy("cleanup policy changed; generate a new plan")
        if (
            candidate["reason"] != "expired"
            and remaining <= report["policy"]["budgetBytes"]
            and volume(Path(record["path"]))[1] >= report["policy"]["lowWaterBytes"]
        ):
            continue
        try:
            deleted.append(storage.delete(record))
            remaining -= record["bytes"]
        except Busy as error:
            skipped.append({"id": record["id"], "reason": str(error)})
        except (OSError, ValueError, KeyError) as error:
            failed.append({"id": record["id"], "error": str(error)})
    return {
        "schemaVersion": 1,
        "deleted": deleted,
        "skipped": skipped,
        "failed": failed,
        "postPlan": plan(storage),
    }


def space_plan(needs: list[tuple[str, Path, int]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for label, path, required in needs:
        key, free = volume(path)
        item = grouped.setdefault(
            key, {"volumeId": key, "availableBytes": free, "requiredBytes": 0, "paths": []}
        )
        item["availableBytes"] = min(free, item["availableBytes"])
        item["requiredBytes"] += required
        item["paths"].append({"role": label, "path": str(path), "requiredBytes": required})
    for item in grouped.values():
        item["shortfallBytes"] = max(0, item["requiredBytes"] - item["availableBytes"])
    return {
        "volumes": list(grouped.values()),
        "allowed": all(not r["shortfallBytes"] for r in grouped.values()),
    }


def maintain(storage: Storage) -> dict[str, Any] | None:
    categories = storage.config["automaticCategories"]
    if not categories:
        return None
    report = plan(storage, scan=True, categories=categories)
    return collect(storage, report)
