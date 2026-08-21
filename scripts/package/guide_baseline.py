"""Keep package-only Guide baselines from leaking into source worktrees."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

RECEIPT_ENV = "AGENT_FRIEND_PACKAGE_GUIDE_BASELINE_JSON"
RECEIPT_SCHEMA_VERSION = 1
GUIDE_RELATIVE_PATH = Path("agent/src/agent/skills/builtin_assets/agent-friend-guide")
MANIFEST_RELATIVE_PATH = Path("agent/src/agent/skills/builtin_assets/manifest.json")


@dataclass(frozen=True)
class GuideBaseline:
    guide_commit: str
    snapshot_sha256: str
    guide_repo: Path
    lifecycle: str

    def to_receipt(self, product_repo: Path) -> dict[str, object]:
        return {
            "schemaVersion": RECEIPT_SCHEMA_VERSION,
            "productRepo": str(product_repo.resolve()),
            "guideRepo": str(self.guide_repo.resolve()),
            "guideCommit": self.guide_commit,
            "snapshotSha256": self.snapshot_sha256,
            "lifecycle": self.lifecycle,
        }


def _load_guide_module(product_repo: Path) -> ModuleType:
    path = product_repo / "scripts" / "agent-friend-guide" / "run.py"
    spec = importlib.util.spec_from_file_location("agent_friend_package_guide", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Guide maintainer from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git_status(product_repo: Path, paths: tuple[Path, ...] | None = None) -> bytes:
    command = ["git", "-C", str(product_repo), "status", "--porcelain=v1", "-z"]
    if paths:
        command.extend(["--", *(path.as_posix() for path in paths)])
    return subprocess.run(command, check=True, capture_output=True).stdout


def _managed_paths() -> tuple[Path, ...]:
    return (GUIDE_RELATIVE_PATH, MANIFEST_RELATIVE_PATH)


def _validate_guide_source(guide: ModuleType, guide_repo: Path, guide_commit: str) -> GuideBaseline:
    resolved = guide_repo.resolve()
    if guide.git_changes(resolved):
        raise RuntimeError("managed Guide repository must be clean before packaging")
    if guide.git_head(resolved) != guide_commit:
        raise RuntimeError("managed Guide HEAD differs from the requested package baseline")
    if not guide.origin_contains_exact_ref(resolved, guide_commit):
        raise RuntimeError("requested Guide package baseline is not visible on origin")
    verified = guide.verify_package(guide.package_root(resolved))
    return GuideBaseline(
        guide_commit=guide_commit,
        snapshot_sha256=str(verified["snapshotSha256"]),
        guide_repo=resolved,
        lifecycle="validated",
    )


def require_committed_baseline(
    product_repo: Path, guide_repo: Path, guide_commit: str
) -> GuideBaseline:
    """Require formal package inputs to already contain the exact Guide baseline."""

    guide = _load_guide_module(product_repo)
    baseline = _validate_guide_source(guide, guide_repo, guide_commit)
    state = guide.builtin_state(product_repo)
    if (
        not state.get("valid")
        or state.get("sourceCommit") != baseline.guide_commit
        or state.get("snapshotSha256") != baseline.snapshot_sha256
    ):
        raise RuntimeError(
            "baseline-update-required: formal package builds require the exact Guide "
            "baseline to be committed before packaging"
        )
    return GuideBaseline(
        guide_commit=baseline.guide_commit,
        snapshot_sha256=baseline.snapshot_sha256,
        guide_repo=baseline.guide_repo,
        lifecycle="committed",
    )


@contextmanager
def transient_baseline(
    product_repo: Path, guide_repo: Path, guide_commit: str
) -> Iterator[GuideBaseline]:
    """Apply a local-test baseline and restore the exact entry worktree afterwards."""

    guide = _load_guide_module(product_repo)
    baseline = _validate_guide_source(guide, guide_repo, guide_commit)
    managed_paths = _managed_paths()
    if _git_status(product_repo, managed_paths):
        raise RuntimeError(
            "package Guide baseline paths are already dirty; refusing to overwrite unowned changes"
        )
    entry_status = _git_status(product_repo)
    target = product_repo / GUIDE_RELATIVE_PATH
    manifest = product_repo / MANIFEST_RELATIVE_PATH
    if not target.is_dir() or not manifest.is_file():
        raise RuntimeError("checked-in Guide baseline is incomplete")
    original_manifest = json.loads(manifest.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="agent-friend-guide-baseline-") as raw_backup:
        backup = Path(raw_backup) / "agent-friend-guide"
        shutil.copytree(target, backup)
        try:
            result = guide.sync(product_repo, baseline.guide_repo, baseline.guide_commit)
            active = GuideBaseline(
                guide_commit=baseline.guide_commit,
                snapshot_sha256=str(result["snapshotSha256"]),
                guide_repo=baseline.guide_repo,
                lifecycle="transient",
            )
            with exported_receipt(product_repo, active):
                yield active
        finally:
            guide.replace_snapshot(product_repo, backup, original_manifest)
            exit_status = _git_status(product_repo)
            if exit_status != entry_status:
                raise RuntimeError(
                    "package Guide baseline cleanup did not restore the entry worktree state"
                )


def inherited_baseline(product_repo: Path) -> GuideBaseline | None:
    raw = os.environ.get(RECEIPT_ENV)
    if not raw:
        return None
    try:
        receipt = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("package Guide baseline receipt is invalid JSON") from exc
    if receipt.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        raise RuntimeError("package Guide baseline receipt schema is unsupported")
    if Path(str(receipt.get("productRepo", ""))).resolve() != product_repo.resolve():
        raise RuntimeError("package Guide baseline receipt belongs to another worktree")
    guide = _load_guide_module(product_repo)
    state = guide.builtin_state(product_repo)
    guide_commit = str(receipt.get("guideCommit", ""))
    snapshot_sha256 = str(receipt.get("snapshotSha256", ""))
    lifecycle = str(receipt.get("lifecycle", ""))
    if lifecycle not in {"transient", "committed"}:
        raise RuntimeError("package Guide baseline receipt lifecycle is invalid")
    if (
        not state.get("valid")
        or state.get("sourceCommit") != guide_commit
        or state.get("snapshotSha256") != snapshot_sha256
    ):
        raise RuntimeError("inherited package Guide baseline no longer matches the worktree")
    return GuideBaseline(
        guide_commit=guide_commit,
        snapshot_sha256=snapshot_sha256,
        guide_repo=Path(str(receipt.get("guideRepo", ""))).resolve(),
        lifecycle=lifecycle,
    )


@contextmanager
def exported_receipt(product_repo: Path, baseline: GuideBaseline) -> Iterator[None]:
    previous = os.environ.get(RECEIPT_ENV)
    os.environ[RECEIPT_ENV] = json.dumps(baseline.to_receipt(product_repo), separators=(",", ":"))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(RECEIPT_ENV, None)
        else:
            os.environ[RECEIPT_ENV] = previous
