"""Package adapter for shared build storage; release artifacts are never GC targets."""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))

from build_storage import Storage
from build_storage.execution import admit, complete_build, require_space, use_target
from build_storage.files import GIB, identity
from build_storage.policy import maintain, plan
from build_storage.reservations import reserve


def package_needs(roots: Any, *, initial: bool = True) -> list[tuple[str, Path, int]]:
    return [
        ("state", roots.state_root, (40 if initial else 8) * GIB),
        ("release-and-symbols", roots.releases_root, 4 * GIB),
        ("temporary", Path(tempfile.gettempdir()), 4 * GIB),
    ]


def package_plan(roots: Any, *, initial: bool = True, purpose: str = "all") -> dict[str, Any]:
    if purpose not in {"runtime-verify", "runtime", "installer", "all", "installer-smoke-build"}:
        raise ValueError("unknown package purpose")
    needs = package_needs(roots, initial=initial)
    storage = Storage(roots.repo_root)
    policy = plan(storage, needs=needs)
    return {
        "schemaVersion": 1,
        "purpose": purpose,
        "paths": {
            "state": str(roots.state_root),
            "work": str(roots.work_root),
            "releases": str(roots.releases_root),
            "temporary": tempfile.gettempdir(),
        },
        "lifecycle": {
            "success": "retain-for-resume"
            if purpose == "runtime"
            else "retain-until-matrix-passes"
            if purpose == "installer-smoke-build"
            else "archive-evidence-and-remove-build-work",
            "failureRetentionHours": storage.config["retentionHours"],
            "cacheReuse": "same-worktree-only",
            "installerResume": "requires-exact-reuse-build-id",
            "releaseArtifacts": "preserved",
        },
        "verification": {
            "frozen": "doctor-and-capability-smoke",
            "installer": "installer-contract-determines-floor-at-execution",
            "guide": "exact-published-baseline-required-at-execution",
            "status": "not-verified-by-this-plan",
        },
        "cleanup": {
            "admission": "automatic-until-build-requirements-met",
            "automaticCategories": storage.config["automaticCategories"],
            "execution": "revalidate-under-lock-before-writing",
        },
        "estimate": "conservative-stage-reserve",
        "allowed": policy["allowed"],
        "space": policy["space"],
        "storage": policy,
        "needs": needs,
    }


@contextmanager
def package_guard(roots: Any, legacy_lock: Any) -> Iterator[None]:
    storage = Storage(roots.repo_root)
    paths = [
        storage.lock_path("package", roots.repo_id),
        storage.lock_path("release", identity(roots.releases_root)),
        storage.lock_path("profile", "installer-smoke"),
        roots.state_root / "locks" / roots.repo_id / "package-workspace.lock",
    ]
    with ExitStack() as stack:
        for path in sorted(set(paths), key=str):
            stack.enter_context(legacy_lock(path))
        yield


def preflight(roots: Any, *, initial: bool = True) -> dict[str, Any]:
    if not initial:
        return require_space(package_needs(roots, initial=False))
    storage = Storage(roots.repo_root)
    return admit(storage, package_needs(roots))


@contextmanager
def build_storage(paths: Any, *, purpose: str, retain: bool) -> Iterator[None]:
    storage = Storage(paths.roots.repo_root)
    cache = paths.roots.work_root / "build-cache"
    succeeded = False
    with (
        reserve(storage, package_plan(paths.roots)["needs"]),
        use_target(storage, cache, "package-cache", purpose=purpose),
        use_target(
            storage, paths.build_dir, "package-build", purpose=purpose, retain=retain
        ) as record,
    ):
        try:
            yield
            succeeded = True
        finally:
            if record is not None:
                evidence = [
                    paths.build_dir / "package-timings.json",
                    paths.build_dir / "runtime-verification.json",
                    paths.generated_config_dir / "build-identity.json",
                    paths.generated_config_dir / "install-profile.json",
                    paths.generated_config_dir / "guide-baseline.json",
                ]
                storage.archive(record, "passed" if succeeded else "failed", evidence)


def finalize(paths: Any, *, remove: bool) -> None:
    storage = Storage(paths.roots.repo_root)
    complete_build(storage, paths.build_dir, remove=remove, outcome="passed")
    maintain(storage)
    # Existing success hook runs inside package_guard after publication and evidence capture.
    from release_retention import Retention

    retention = Retention(storage, paths.roots.releases_root)
    published = [
        r
        for r in retention.inventory()["objects"]
        if r.get("manifest", {}).get("build_id") == paths.build_id
    ]
    if len(published) > 1:
        raise ValueError("ambiguous published local-test build identity")
    if published:
        report = maintain_releases(paths.roots, Path(published[0]["path"]))
        print("local_test_retention: " + json.dumps(report, ensure_ascii=False))
        if report.get("failed"):
            raise RuntimeError(
                "installer published, but local-test maintenance failed; inspect report"
            )


def maintain_releases(roots: Any, directory: Path) -> dict[str, Any]:
    """Caller holds package_guard, including the release-root lock, after successful publication."""
    from release_retention import Retention

    retention = Retention(Storage(roots.repo_root), roots.releases_root)
    retention.register(directory, apply=True, locked=True)
    if not retention.state()["enabled"]:
        return {"enabled": False, "deleted": []}
    return retention.collect(retention.inventory(), locked=True)


def emit_plan(roots: Any, *, purpose: str = "all") -> None:
    result = package_plan(roots, purpose=purpose)
    result.pop("needs")
    print(json.dumps(result, ensure_ascii=False, indent=2))
