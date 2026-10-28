"""Harness adapters: owned child lifetime, volume admission, and result evidence."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .files import GIB, MARKER, identity, no_links, read_json, safe_directory, write_json
from .locks import Busy, lock
from .policy import collect, maintain, plan, space_plan
from .reservations import reserve
from .store import Storage


def require_space(needs: list[tuple[str, Path, int]]) -> dict[str, Any]:
    report = space_plan(needs)
    if not report["allowed"]:
        raise RuntimeError(f"insufficient build space: {report}")
    return report


def admit(storage: Storage, needs: list[tuple[str, Path, int]]) -> dict[str, Any]:
    report = plan(storage, needs=needs)
    cleanup = None
    if not report["allowed"]:
        report = plan(storage, scan=True, needs=needs)
        if not report["complete"]:
            raise RuntimeError(f"build admission blocked by incomplete inventory: {report}")
        if not report["allowed"]:
            cleanup = collect(storage, report, needs=needs)
            report = cleanup["postPlan"]
            print(
                f"build cache cleanup: deleted={len(cleanup['deleted'])} "
                f"skipped={len(cleanup['skipped'])} failed={len(cleanup['failed'])}",
                file=sys.stderr,
            )
    if not report["allowed"]:
        raise RuntimeError(
            f"build admission blocked after safe cleanup: {report}; cleanup={cleanup}"
        )
    return {"allowed": True, "storage": report, "space": report["space"], "cleanup": cleanup}


@contextmanager
def use_target(
    storage: Storage, target: Path, category: str, *, purpose: str = "build", retain: bool = False
) -> Iterator[dict[str, Any] | None]:
    """Old nonempty directories may still be used, but never implicitly adopted or GC'd."""
    target = safe_directory(target, storage.repo, storage.control)
    if target.exists() and any(target.iterdir()) and not (target / MARKER).exists():
        with lock(storage.object_lock(identity(target))):
            yield None
        return
    with storage.use(target, category, purpose=purpose, retain=retain) as record:
        yield record


def run_cargo(
    repo: Path,
    category: str,
    command: list[str],
    *,
    target: Path | None = None,
    cwd: Path | None = None,
    runtime_owner_pid: int | None = None,
) -> int:
    storage = Storage(repo)
    override = target or (
        Path(os.environ["CARGO_TARGET_DIR"]) if os.environ.get("CARGO_TARGET_DIR") else None
    )
    actual = storage.target(category, override)
    # Admission for dev/check is independent of the stricter 40 GiB package floor.
    admit(storage, [(category, actual, 12 * GIB)])
    env = {**os.environ, "CARGO_TARGET_DIR": str(actual)}
    with (
        reserve(storage, [(category, actual, 12 * GIB)]),
        use_target(storage, actual, category) as record,
    ):
        if runtime_owner_pid is not None:
            if category != "dev" or record is None:
                raise ValueError("runtime lease binding requires a managed dev target")
            sys.path.insert(0, str(repo / "scripts/dev"))
            from runtime_guard import bind_storage_operation

            bind_storage_operation(repo, runtime_owner_pid, record["operationId"])
        code = subprocess.call(command, cwd=cwd or repo, env=env)
        if record is not None:
            storage.archive(record, f"exit:{code}", [])
        if code:
            raise subprocess.CalledProcessError(code, command)
    maintain(storage)
    return code


def complete_build(storage: Storage, path: Path, *, remove: bool, outcome: str) -> None:
    record_path = storage.record_path(identity(path))
    if not record_path.exists():
        # Legacy cleanup remains owned by the package adapter, not this generic helper.
        return
    record = read_json(record_path)
    storage.archive(
        record,
        outcome,
        [
            path / name
            for name in (
                "build-contract.json",
                "build-identity.json",
                "package-timings.json",
            )
        ],
    )
    if remove:
        with lock(storage.object_lock(record["id"])):
            record = read_json(record_path)
            if record["state"] == "running":
                raise Busy("cannot finalize an active build")
            record["retainUntil"] = None
            write_json(record_path, record)
        storage.delete(record)


def adopt_legacy_package(
    storage: Storage, path: Path, *, apply: bool = False, allow_cargo_evidence: bool = False
) -> dict[str, Any]:
    """Adopt proven legacy objects; Cargo-only fallback requires explicit opt-in."""
    from .legacy_cargo import cargo_evidence, legacy_identity

    target = safe_directory(path, storage.repo, storage.control)
    workspace = target.parent
    state = workspace / "workspace-state.json"
    no_links(state)
    evidence = None
    if state.exists():
        receipt = read_json(state)
    elif allow_cargo_evidence:
        evidence = cargo_evidence(target, storage.repo_id[:12])
        receipt = evidence
    else:
        raise ValueError(
            "workspace receipt missing; preview explicit --cargo-evidence for Cargo-only caches"
        )

    if (
        receipt.get("worktreeId") != workspace.name
        or receipt.get("repoId") != workspace.parent.name
    ):
        raise ValueError("legacy package workspace identity mismatch")
    source = Path(receipt["repoRoot"])
    if legacy_identity(source) != workspace.name:
        raise ValueError("legacy worktree path mismatch")
    common = subprocess.check_output(
        ["git", "-C", str(storage.repo), "rev-parse", "--git-common-dir"], text=True
    ).strip()
    if legacy_identity(storage.repo / common) != receipt["repoId"]:
        raise ValueError("legacy workspace is not owned by this repository")
    if target.name == "build-cache":
        if any(p.name not in {"cargo-target", "pyinstaller"} for p in target.iterdir()):
            raise ValueError("unexpected legacy cache contents")
        category = "package-cache"
    else:
        contract = read_json(target / "config/build-identity.json")
        if contract.get("buildId") != target.name:
            raise ValueError("legacy build contract does not identify this directory")
        category = "package-build"
    result = {
        "schemaVersion": 1,
        "path": str(target),
        "category": category,
        "source": str(state) if evidence is None else evidence["witnesses"][0]["path"],
        "adopted": False,
        "evidence": evidence,
    }
    if apply:
        # Retain old-build protection until explicit human confirmation; never infer inactivity from mtime.
        with (
            lock(storage.lock_path("package", storage.repo_id[:12])),
            lock(
                workspace.parent.parent.parent
                / "locks"
                / workspace.parent.name
                / "package-workspace.lock"
            ),
            lock(storage.object_lock(identity(target))),
        ):
            if evidence is not None:
                if cargo_evidence(target, storage.repo_id[:12]) != evidence:
                    raise Busy("Cargo evidence changed during adoption")
                # Persist the proof outside the disposable cache before writing its ownership marker.
                write_json(
                    storage.control / "adoptions" / storage.repo_id / f"{identity(target)}.json",
                    {"schemaVersion": 1, "path": str(target), "evidence": evidence},
                )
            record = storage.register(target, category, adopted=True)
            record.update(
                worktree=str(source),
                pin="legacy adoption: verify old consumers stopped before unpin",
                lastUsedAt=time.time(),
                adoptionEvidence=evidence or {"kind": "workspace-receipt", "path": str(state)},
            )
            write_json(storage.record_path(record["id"]), record)
            result.update(adopted=True, objectId=record["id"], pin=record["pin"])
    return result
