"""Owned build objects, measured inventories, and conservative lifecycle transitions."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from .config import CATEGORIES, control_root, default_build_root, load_config
from .files import (
    MARKER,
    identity,
    read_json,
    safe_directory,
    tree_size,
    volume,
    write_json,
)
from .locks import Busy, lock


class Storage:
    def __init__(self, repo: Path, *, control: Path | None = None, common: Path | None = None):
        self.repo = repo.resolve()
        self.control = control if control is not None else control_root()
        if common is None:
            raw = subprocess.check_output(
                ["git", "-C", str(self.repo), "rev-parse", "--git-common-dir"],
                text=True,
                encoding="utf-8",
            ).strip()
            common = (self.repo / raw).resolve()
        self.repo_id = identity(common)
        self.worktree_id = identity(self.repo)
        self.records = self.control / "objects" / self.repo_id

    @property
    def config(self) -> dict[str, Any]:
        return load_config(self.control)

    def lock_path(self, scope: str, key: str) -> Path:
        return self.control / "locks" / scope / f"{key}.lock"

    def object_lock(self, object_id: str) -> Path:
        if len(object_id) != 24 or any(c not in "0123456789abcdef" for c in object_id):
            raise ValueError("invalid object ID")
        return self.lock_path("objects", object_id)

    def record_path(self, object_id: str) -> Path:
        self.object_lock(object_id)
        return self.records / f"{object_id}.json"

    def target(self, category: str, override: Path | None = None) -> Path:
        if category not in CATEGORIES:
            raise ValueError("unknown build storage category")
        base = Path(self.config["buildRoot"] or default_build_root())
        return safe_directory(
            override or base / category / self.repo_id / self.worktree_id,
            self.repo,
            self.control,
        )

    def configure(self, changes: dict[str, Any]) -> dict[str, Any]:
        with lock(self.lock_path("configuration", "settings")):
            value = {**self.config, **changes}
            if value.get("buildRoot") is not None:
                value["buildRoot"] = str(
                    safe_directory(Path(value["buildRoot"]), self.repo, self.control)
                )
            # Validate before atomic publication without mutating the live config on failure.
            import math

            from .config import DEFAULTS

            if set(value) != set(DEFAULTS):
                raise ValueError("unknown configuration fields")
            for key in ("budgetBytes", "lowWaterBytes", "cacheDays", "retentionHours"):
                number = value[key]
                if (
                    isinstance(number, bool)
                    or not isinstance(number, (int, float))
                    or not math.isfinite(number)
                    or number <= 0
                ):
                    raise ValueError(f"invalid {key}")
            if not isinstance(value["automaticCategories"], list) or any(
                c not in CATEGORIES for c in value["automaticCategories"]
            ):
                raise ValueError("unknown cleanup category")
            write_json(self.control / "config.json", value)
        return value

    def _validate(self, record: dict[str, Any]) -> Path:
        target = safe_directory(Path(record["path"]), self.repo, self.control)
        if record["repoId"] != self.repo_id or record["id"] != identity(target):
            raise ValueError("build object identity mismatch")
        if record["category"] not in CATEGORIES:
            raise ValueError("invalid build object category")
        marker = read_json(target / MARKER)
        for key in ("id", "repoId", "path", "category", "ownershipToken"):
            if record[key] != marker.get(key):
                raise ValueError(f"build ownership mismatch: {key}")
        return target

    def register(self, path: Path, category: str, *, adopted: bool = False) -> dict[str, Any]:
        target = safe_directory(path, self.repo, self.control)
        if category not in CATEGORIES:
            raise ValueError("invalid build object category")
        object_id = identity(target)
        with lock(self.lock_path("registry", self.repo_id)):
            existing = self.record_path(object_id)
            if existing.exists():
                record = read_json(existing)
                self._validate(record)
                if record["category"] != category:
                    raise ValueError("object category cannot be changed implicitly")
                return record
            # Forbid overlapping independently deletable objects.
            for p in self.records.glob("*.json"):
                other = read_json(p)
                other_path = Path(other["path"])
                if target.is_relative_to(other_path) or other_path.is_relative_to(target):
                    raise ValueError(f"overlapping build objects: {target} and {other_path}")
            if target.exists() and any(target.iterdir()) and not adopted:
                raise ValueError(
                    f"existing unmanaged build directory needs explicit adoption: {target}"
                )
            if adopted:
                tree_size(target)  # Full link/error check before accepting explicit ownership.
            target.mkdir(parents=True, exist_ok=True)
            now = time.time()
            record = {
                "schemaVersion": 1,
                "id": object_id,
                "repoId": self.repo_id,
                "worktree": str(self.repo),
                "path": str(target),
                "category": category,
                "ownershipToken": uuid.uuid4().hex,
                "createdAt": now,
                "lastUsedAt": now,
                "state": "idle",
                "retainUntil": None,
                "pin": None,
                "generation": 0,
                "bytes": None,
                "measuredAt": None,
                "measurementGeneration": None,
            }
            write_json(target / MARKER, record)
            write_json(existing, record)
            return record

    def inventory(
        self, *, scan: bool = False, categories: list[str] | None = None
    ) -> dict[str, Any]:
        objects = []
        complete = True
        for p in sorted(self.records.glob("*.json")):
            try:
                record = read_json(p)
                if categories is not None and record["category"] not in categories:
                    continue
                target = self._validate(record)
                if scan and record["state"] != "running":
                    try:
                        with lock(self.object_lock(record["id"])):
                            record = read_json(p)
                            self._validate(record)
                            if record["state"] == "running":
                                raise Busy("operation state is unresolved")
                            import sys

                            print(f"scan: {target}", file=sys.stderr, flush=True)
                            record.update(
                                bytes=tree_size(target),
                                measuredAt=time.time(),
                                measurementGeneration=record["generation"],
                            )
                            write_json(p, record)
                    except Busy:
                        record["scanSkipped"] = "protected-active"
                record["sizeStatus"] = (
                    "unknown"
                    if record["bytes"] is None
                    else "stale"
                    if record["measurementGeneration"] != record["generation"]
                    or record["state"] == "running"
                    else "measured"
                )
                record["volumeId"], record["availableBytes"] = volume(target)
                objects.append(record)
            except KeyboardInterrupt:
                complete = False
                break
            except (OSError, ValueError, KeyError) as error:
                objects.append(
                    {
                        "record": str(p),
                        "state": "unmanaged",
                        "error": str(error),
                        "bytes": None,
                        "sizeStatus": "unknown",
                    }
                )
                complete = False
        return {
            "schemaVersion": 1,
            "repoId": self.repo_id,
            "complete": complete,
            "sizeAccounting": "logical-file-bytes",
            "objects": objects,
            "policy": self.config,
            "reservations": [
                read_json(p) for p in sorted((self.control / "reservations").glob("*.json"))
            ],
        }

    def pin(self, object_id: str, reason: str | None) -> None:
        with lock(self.object_lock(object_id)):
            p = self.record_path(object_id)
            record = read_json(p)
            self._validate(record)
            if record["state"] == "running":
                raise Busy("cannot change retention of unresolved operation")
            record["pin"] = reason
            write_json(p, record)

    def reconcile(
        self, operation_id: str, *, reason: str, outcome: str = "failed"
    ) -> dict[str, Any]:
        """Explicit operator assertion after checking the entire child process tree has stopped."""
        if len(operation_id) != 32 or any(c not in "0123456789abcdef" for c in operation_id):
            raise ValueError("invalid operation ID")
        if not reason.strip():
            raise ValueError("reconciliation requires the operator's stopped-process evidence")
        if outcome not in {"stopped", "cancelled", "failed"}:
            raise ValueError("invalid reconciliation outcome")
        records = [read_json(p) for p in self.records.glob("*.json")]
        affected = [r for r in records if r.get("operationId") == operation_id]
        reservation = self.control / "reservations" / f"{operation_id}.json"
        with ExitStack() as stack:
            stack.enter_context(lock(self.lock_path("admission", "volumes")))
            for row in sorted(affected, key=lambda r: r["id"]):
                stack.enter_context(lock(self.object_lock(row["id"])))
            if reservation.exists() and read_json(reservation)["repoId"] != self.repo_id:
                raise ValueError("operation belongs to another repository")
            for row in affected:
                current = read_json(self.record_path(row["id"]))
                self._validate(current)
                if current.get("operationId") != operation_id:
                    raise Busy("operation changed during reconciliation")
            completed = []
            for row in affected:
                row = read_json(self.record_path(row["id"]))
                if row["state"] != "running":
                    continue  # Already finalized; never renew retention on repeated reconciliation.
                ended = time.time()
                self.archive(row, outcome, [])
                row.update(
                    state="idle" if outcome == "stopped" else "failed",
                    outcome=outcome,
                    endedAt=ended,
                    lastUsedAt=ended,
                    ownerPid=None,
                    reconciliation=reason,
                    retainUntil=None
                    if outcome == "stopped"
                    else ended + self.config["retentionHours"] * 3600,
                )
                write_json(self.record_path(row["id"]), row)
                completed.append(row["id"])
            reservation.unlink(missing_ok=True)
        return {
            "schemaVersion": 1,
            "operationId": operation_id,
            "reconciled": completed,
        }

    @contextmanager
    def use(
        self, path: Path, category: str, *, purpose: str = "build", retain: bool = False
    ) -> Iterator[dict[str, Any]]:
        target = safe_directory(path, self.repo, self.control)
        with lock(self.object_lock(identity(target))):
            record = self.register(target, category)
            if record["state"] == "running":
                raise Busy(f"previous operation not reconciled: {target}")
            if record["bytes"] is None or record["measurementGeneration"] != record["generation"]:
                record.update(
                    bytes=tree_size(target),
                    measuredAt=time.time(),
                    measurementGeneration=record["generation"],
                )
            record.update(
                state="running",
                purpose=purpose,
                ownerPid=os.getpid(),
                operationId=getattr(self, "operation_id", None) or uuid.uuid4().hex,
                outcome=None,
                endedAt=None,
                lastUsedAt=time.time(),
                generation=record["generation"] + 1,
            )
            write_json(self.record_path(record["id"]), record)
            succeeded = False
            interrupted = False
            try:
                yield record
                succeeded = True
            except (KeyboardInterrupt, SystemExit):
                interrupted = True
                raise
            finally:
                # An uncatchable parent crash leaves running and is never auto-reaped by PID alone.
                record.update(
                    state="awaiting-resume"
                    if succeeded and retain
                    else "idle"
                    if succeeded
                    else "failed",
                    lastUsedAt=time.time(),
                    ownerPid=None,
                    outcome="succeeded" if succeeded else "cancelled" if interrupted else "failed",
                    endedAt=time.time() if not interrupted else None,
                    retainUntil=time.time() + self.config["retentionHours"] * 3600
                    if retain or not succeeded
                    else None,
                )
                if interrupted:
                    record.update(state="running", interruption="child-lifetime-unconfirmed")
                try:
                    record.update(
                        bytes=tree_size(target),
                        measuredAt=time.time(),
                        measurementGeneration=record["generation"],
                    )
                except (OSError, ValueError):
                    record["measurementGeneration"] = None
                write_json(self.record_path(record["id"]), record)

    def archive(self, record: dict[str, Any], outcome: str, files: list[Path]) -> Path:
        report = {
            "schemaVersion": 1,
            "objectId": record["id"],
            "operationId": record.get("operationId"),
            "outcome": outcome,
            "recordedAt": time.time(),
            "object": {
                k: record.get(k)
                for k in (
                    "id",
                    "path",
                    "repoId",
                    "category",
                    "worktree",
                    "generation",
                    "purpose",
                    "outcome",
                    "endedAt",
                )
            },
            "error": str(sys.exc_info()[1])[:4096] if sys.exc_info()[1] is not None else None,
            "evidence": {},
        }
        for p in files:
            if p.is_file():
                if p.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError(f"evidence too large to archive as metadata: {p}")
                report["evidence"][p.name] = p.read_text(encoding="utf-8")
        destination = (
            self.control
            / "reports"
            / self.repo_id
            / f"{record['id']}-{record.get('operationId') or uuid.uuid4().hex}.json"
        )
        if destination.exists():
            previous = read_json(destination)
            if previous["objectId"] != report["objectId"]:
                raise ValueError("report belongs to a different build object")
            report["evidence"] = {**previous["evidence"], **report["evidence"]}
            if not files:
                report["outcome"] = previous["outcome"]
        write_json(destination, report)
        return destination

    def delete(self, expected: dict[str, Any]) -> dict[str, Any]:
        object_id = expected["id"]
        with ExitStack() as stack:
            if expected.get("category", "").startswith("package-"):
                stack.enter_context(lock(self.lock_path("package", self.repo_id[:12])))
                target = Path(expected["path"])
                if len(target.parents) >= 4 and target.parents[2].name == "work":
                    stack.enter_context(
                        lock(
                            target.parents[3]
                            / "locks"
                            / target.parents[1].name
                            / "package-workspace.lock"
                        )
                    )
            stack.enter_context(lock(self.object_lock(object_id)))
            p = self.record_path(object_id)
            record = read_json(p)
            if any(
                record.get(k) != expected.get(k)
                for k in ("generation", "pin", "retainUntil", "lastUsedAt", "ownershipToken")
            ):
                raise Busy("object changed since cleanup plan")
            if (
                record["state"] == "running"
                or record["pin"]
                or (record["retainUntil"] or 0) > time.time()
            ):
                raise Busy("object is protected")
            target = self._validate(record)
            tree_size(target)  # Reject nested reparse points before any recursive mutation.
            # Persist provenance before touching files. Failure here leaves the whole object intact.
            evidence = (
                [
                    target / name
                    for name in (
                        "config/build-identity.json",
                        "config/install-profile.json",
                        "config/guide-baseline.json",
                        "package-timings.json",
                        "runtime-verification.json",
                    )
                ]
                if record["category"] == "package-build"
                else []
            )
            self.archive(record, record.get("outcome") or record["state"], evidence)
            before = volume(target)[1]
            try:
                # Preserve ownership until all other children are removed, so partial cleanup retries work.
                for child in target.iterdir():
                    if child.name == MARKER:
                        continue
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                (target / MARKER).unlink()
                try:
                    target.rmdir()
                except OSError:
                    write_json(target / MARKER, record)
                    raise
                p.unlink()
            except OSError as error:
                record.update(
                    state="cleanup-failed", cleanupError=str(error), measurementGeneration=None
                )
                write_json(p, record)
                raise
            return {
                "id": object_id,
                "path": str(target),
                "availableBytesBefore": before,
                "availableBytesAfter": volume(target)[1],
            }
