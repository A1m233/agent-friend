"""Capacity decisions and volume accounting, shared by preview and execution."""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .files import volume
from .locks import Busy
from .store import Storage


def plan(
    storage: Storage,
    *,
    scan: bool = False,
    categories: list[str] | None = None,
    needs: list[tuple[str, Path, int]] | None = None,
) -> dict[str, Any]:
    inventory = storage.inventory(scan=scan)
    config = inventory["policy"]
    rows = inventory["objects"]
    known = sum(r.get("bytes") or 0 for r in rows)
    reservations = inventory["reservations"]
    operations = {r["operationId"]: r for r in reservations if r["repoId"] == storage.repo_id}
    # A running target's last measurement plus its operation's growth reservation is
    # an estimate, not a fresh measurement. Count each reservation once, even when
    # one operation owns both a package cache and a build directory.
    estimated = [
        r["id"]
        for r in rows
        if r["state"] == "running"
        and r.get("bytes") is not None
        and r.get("operationId") in operations
        and any(
            v["volumeId"] == r["volumeId"] and v["requiredBytes"] > 0
            for v in operations[r["operationId"]]["volumes"]
        )
    ]
    unknown = [
        r.get("id", r.get("record"))
        for r in rows
        if r["sizeStatus"] != "measured" and r.get("id") not in estimated
    ]
    reserved = sum(v["requiredBytes"] for r in operations.values() for v in r["volumes"])
    pressure = known + reserved > config["budgetBytes"]
    space = space_plan(needs, reservations=reservations) if needs is not None else None
    short_volumes = (
        {v["volumeId"] for v in space["volumes"] if v["shortfallBytes"]}
        if space is not None
        else set()
    )
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
        volume_pressure = (
            row["volumeId"] in short_volumes
            if space is not None
            else row["availableBytes"] < config["lowWaterBytes"]
        )
        if pressure or volume_pressure or (expired and space is None):
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
        "reservedGrowthBytes": reserved,
        "estimatedObjects": estimated,
        "unknownObjects": unknown,
        "complete": inventory["complete"],
        "budgetSatisfied": inventory["complete"] and not unknown and not pressure,
        "allowed": inventory["complete"]
        and not unknown
        and not pressure
        and (space is None or space["allowed"]),
        "space": space,
        "candidates": candidates,
        "protected": protected,
        "policy": config,
    }


def collect(
    storage: Storage,
    report: dict[str, Any],
    *,
    needs: list[tuple[str, Path, int]] | None = None,
) -> dict[str, Any]:
    deleted, skipped, failed = [], [], []
    remaining = report["knownLogicalBytes"]
    for candidate in report["candidates"]:
        record = candidate["object"]
        if storage.config != report["policy"]:
            raise Busy("cleanup policy changed; generate a new plan")
        if needs is not None:
            current = plan(storage, needs=needs)
            if current["allowed"]:
                break
            # Do not reclaim another volume just because the build volume is full,
            # or delete useful caches to try to resolve an unknown measurement.
            if record["id"] not in {c["object"]["id"] for c in current["candidates"]}:
                continue
        elif (
            candidate["reason"] != "expired"
            and remaining + report["reservedGrowthBytes"] <= report["policy"]["budgetBytes"]
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
        "postPlan": plan(storage, needs=needs),
    }


def space_plan(
    needs: list[tuple[str, Path, int]], *, reservations: Sequence[dict[str, Any]] = ()
) -> dict[str, Any]:
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
        item["reservedBytes"] = sum(
            v["requiredBytes"]
            for r in reservations
            for v in r["volumes"]
            if v["volumeId"] == item["volumeId"]
        )
        item["shortfallBytes"] = max(
            0, item["requiredBytes"] + item["reservedBytes"] - item["availableBytes"]
        )
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
