"""Opt-in local-test retention, coordinated with the existing release-root lock."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from build_storage.files import identity, no_links, read_json, safe_directory, volume, write_json
from build_storage.locks import Busy, lock
from build_storage.store import Storage
from releases import local_test_release_dir, sha256, validate_release_directory


class Retention:
    def __init__(self, storage: Storage, root: Path):
        self.storage = storage
        self.root = safe_directory(root, storage.repo, storage.control)
        self.directory = storage.control / "release-retention" / identity(self.root)
        self.state_path = self.directory / "state.json"
        self.lock_path = storage.lock_path("release", identity(self.root))

    def state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "schemaVersion": 1,
                "root": str(self.root),
                "enabled": False,
                "keepCount": 3,
                "keepDays": 14,
                "generation": 0,
                "artifacts": {},
            }
        value = read_json(self.state_path)
        if value["root"] != str(self.root) or type(value["enabled"]) is not bool:
            raise ValueError("invalid release retention state")
        for key in ("keepCount", "keepDays"):
            if type(value[key]) is not int or value[key] < 1:
                raise ValueError(f"invalid retention {key}")
        return value

    def journal_path(self, relative: str) -> Path:
        return self.directory / "deletions" / f"{identity(self.root / relative)}.json"

    def inspect(self, directory: Path, *, verify: bool = False) -> dict[str, Any]:
        directory = no_links(directory)
        if not directory.is_relative_to(self.root / "_local-test"):
            raise ValueError("not a local-test artifact directory")
        manifest_path = no_links(directory / "release.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = local_test_release_dir(
            self.root, str(manifest["version"]), manifest["distribution"], manifest["build_id"]
        )
        if (
            directory != expected
            or manifest["build_mode"] != "local-test"
            or manifest["publishable"] is not False
        ):
            raise ValueError("local-test layout/manifest mismatch")
        if manifest["schema_version"] != 2 or manifest["platform"] != "windows-x64":
            raise ValueError("unsupported artifact manifest")
        name = manifest["installer"]["name"]
        if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError("invalid installer name")
        allowed = {
            "release.json",
            name,
            f"{name}.sha256",
            "LOCAL-TEST-NOT-FOR-DISTRIBUTION.txt",
            "SENSITIVE-DO-NOT-PUBLISH.txt",
        }
        size = 0
        files = []
        for p in directory.iterdir():
            no_links(p)
            if not p.is_file() or p.name not in allowed:
                raise ValueError("unexpected artifact contents")
            size += p.stat().st_size
            files.append(p)
        if not (directory / name).is_file():
            raise ValueError("installer missing")
        created = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("artifact timestamp lacks timezone")
        profile = manifest.get("profile_id", "production")
        if profile not in {"production", "installer-smoke"}:
            raise ValueError("unknown artifact profile")
        if verify:
            validate_release_directory(directory, formal=False, releases_root=self.root)
        return {
            "path": str(directory),
            "relative": directory.relative_to(self.root).as_posix(),
            "manifestSha256": sha256(manifest_path),
            "manifest": manifest,
            "createdAt": created.timestamp(),
            "bytes": size,
            "group": [manifest["platform"], manifest["distribution"], profile],
            "files": {p.name: sha256(p) for p in files} if verify else None,
        }

    def inventory(self, *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        state = self.state() if policy is None else policy
        rows, errors = [], []
        base = self.root / "_local-test"
        pending = [(base, 0)] if base.exists() else []
        while pending:
            directory, depth = pending.pop()
            try:
                no_links(directory)
                if depth < 4:
                    for p in directory.iterdir():
                        no_links(p)
                        if not p.is_dir():
                            raise ValueError("unexpected file in local-test layout")
                        pending.append((p, depth + 1))
                else:
                    relative = directory.relative_to(self.root).as_posix()
                    journal = self.journal_path(relative)
                    if journal.exists():
                        receipt = read_json(journal)
                        rows.append(
                            {
                                "path": str(directory),
                                "relative": relative,
                                "reason": "deletion-" + receipt["status"],
                                "bytes": None,
                            }
                        )
                    else:
                        rows.append(self.inspect(directory))
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append({"path": str(directory), "error": str(error)})
        ranked: dict[tuple, list[dict]] = {}
        for row in rows:
            if "group" in row:
                ranked.setdefault(tuple(row["group"]), []).append(row)
        for group in ranked.values():
            group.sort(key=lambda r: (-r["createdAt"], r["relative"]))
            for rank, row in enumerate(group):
                entry = state["artifacts"].get(row["relative"])
                row["registered"] = entry is not None
                row["reason"] = (
                    "unmanaged"
                    if entry is None
                    else "identity-changed"
                    if entry["manifestSha256"] != row["manifestSha256"]
                    else "pinned"
                    if entry.get("pin")
                    else "verification-profile"
                    if row["group"][2] == "installer-smoke"
                    else "recent-count"
                    if rank < state["keepCount"]
                    else "recent-age"
                    if time.time() - row["createdAt"] < state["keepDays"] * 86400
                    else "expired"
                )
        return {
            "schemaVersion": 1,
            "root": str(self.root),
            "policy": state,
            "objects": rows,
            "errors": errors,
            "complete": not errors,
            "candidates": [r for r in rows if r["reason"] == "expired"],
            "sizeAccounting": "logical-file-bytes",
            "observedAt": time.time(),
        }

    def configure(
        self,
        *,
        enabled: bool | None = None,
        count: int | None = None,
        days: int | None = None,
        apply: bool = True,
    ) -> dict[str, Any]:
        if not apply:
            state = self.state()
            for key, value in (("keepCount", count), ("keepDays", days)):
                if value is not None:
                    if type(value) is not int or value < 1:
                        raise ValueError(f"{key} must be a positive integer")
                    state[key] = value
            if enabled is not None:
                state["enabled"] = enabled
            return {**self.inventory(policy=state), "applied": False}
        with lock(self.lock_path):
            state = self.state()
            for key, value in (("keepCount", count), ("keepDays", days)):
                if value is not None:
                    if type(value) is not int or value < 1:
                        raise ValueError(f"{key} must be a positive integer")
                    state[key] = value
            if enabled is not None:
                state["enabled"] = enabled
            state["generation"] += 1
            write_json(self.state_path, state)
            return self.inventory()

    def register(
        self, directory: Path, *, apply: bool = False, locked: bool = False
    ) -> dict[str, Any]:
        if not locked:
            with lock(self.lock_path):
                return self.register(directory, apply=apply, locked=True)
        row = self.inspect(directory, verify=True)
        state = self.state()
        if apply:
            previous = state["artifacts"].get(row["relative"])
            if previous and previous["manifestSha256"] != row["manifestSha256"]:
                raise ValueError("registered artifact identity changed")
            state["artifacts"].setdefault(
                row["relative"], {"manifestSha256": row["manifestSha256"], "pin": None}
            )
            state["generation"] += 1
            write_json(self.state_path, state)
        return {"artifact": row, "registered": apply}

    def pin(self, directory: Path, reason: str | None) -> None:
        with lock(self.lock_path):
            row = self.inspect(directory, verify=True)
            state = self.state()
            entry = state["artifacts"].get(row["relative"])
            if entry is None or entry["manifestSha256"] != row["manifestSha256"]:
                raise ValueError("adopt the exact artifact before changing its pin")
            entry["pin"] = reason
            state["generation"] += 1
            write_json(self.state_path, state)

    def collect(self, preview: dict[str, Any], *, locked: bool = False) -> dict[str, Any]:
        if not locked:
            with lock(self.lock_path):
                return self.collect(preview, locked=True)
        # Unknown directories stay protected; they do not acquire ownership or become candidates.
        result: dict[str, Any] = {
            "deleted": [],
            "skipped": [],
            "failed": [],
            "discoveryErrors": preview["errors"],
        }
        if self.state() != preview["policy"]:
            raise Busy("retention state changed; regenerate preview")
        for expected in preview["candidates"]:
            try:
                fresh = self.inventory()
                current = next(
                    (r for r in fresh["candidates"] if r["relative"] == expected["relative"]), None
                )
                if current is None or current["manifestSha256"] != expected["manifestSha256"]:
                    raise Busy("artifact is no longer the planned candidate")
                row = self.inspect(Path(expected["path"]), verify=True)
                receipt = {
                    "schemaVersion": 1,
                    "root": str(self.root),
                    "relative": row["relative"],
                    "manifest": row["manifest"],
                    "files": row["files"],
                    "status": "pending",
                    "startedAt": time.time(),
                    "policy": preview["policy"],
                    "availableBytesBefore": volume(self.root)[1],
                }
                write_json(self.journal_path(row["relative"]), receipt)
                result["deleted"].append(self._finish_delete(receipt))
            except Busy as error:
                result["skipped"].append({"path": expected["path"], "reason": str(error)})
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                result["failed"].append({"path": expected["path"], "error": str(error)})
        return result

    def _finish_delete(self, receipt: dict[str, Any]) -> dict[str, Any]:
        relative = receipt["relative"]
        directory = no_links(self.root / relative)
        manifest = receipt["manifest"]
        expected = local_test_release_dir(
            self.root, manifest["version"], manifest["distribution"], manifest["build_id"]
        )
        if (
            directory != expected
            or not directory.is_relative_to(self.root / "_local-test")
            or receipt["root"] != str(self.root)
        ):
            raise ValueError("deletion receipt escaped local-test root")
        if directory.exists():
            for p in directory.iterdir():
                no_links(p)
                if not p.is_file() or receipt["files"].get(p.name) != sha256(p):
                    raise ValueError("remaining artifact content changed; cleanup refused")
            try:
                for name in sorted(receipt["files"], key=lambda n: n == "release.json"):
                    if Path(name).name != name:
                        raise ValueError("invalid deletion receipt filename")
                    (directory / name).unlink(missing_ok=True)
                directory.rmdir()
            except OSError as error:
                receipt.update(status="failed", error=str(error))
                write_json(self.journal_path(relative), receipt)
                raise
        receipt.update(
            status="completed", endedAt=time.time(), availableBytesAfter=volume(self.root)[1]
        )
        write_json(self.journal_path(relative), receipt)
        return {
            "relative": relative,
            "receipt": str(self.journal_path(relative)),
            "availableBytesBefore": receipt["availableBytesBefore"],
            "availableBytesAfter": receipt["availableBytesAfter"],
        }

    def retry(self, directory: Path) -> dict[str, Any]:
        with lock(self.lock_path):
            relative = no_links(directory).relative_to(self.root).as_posix()
            receipt = read_json(self.journal_path(relative))
            if receipt["relative"] != relative:
                raise ValueError("deletion receipt identity mismatch")
            return self._finish_delete(receipt)
