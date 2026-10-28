from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "_lib"))

from build_storage import Storage
from build_storage.files import read_json
from build_storage.locks import Busy
from release_retention import Retention
from releases import local_test_release_dir, sha256


@pytest.fixture
def retention(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return Retention(
        Storage(repo, control=tmp_path / "control", common=repo / ".git"), tmp_path / "releases"
    )


def artifact(retention, number, *, days=30, profile="production", distribution="ordinary"):
    build_id = f"build-{number}"
    folder = local_test_release_dir(retention.root, "0.4.1", distribution, build_id)
    folder.mkdir(parents=True)
    setup = folder / "setup.exe"
    setup.write_bytes(f"test installer {number}".encode())
    manifest = {
        "schema_version": 2,
        "product": "agent-friend" if profile == "production" else "agent-friend-installer-smoke",
        "version": "0.4.1",
        "platform": "windows-x64",
        "build_mode": "local-test",
        "distribution": distribution,
        "publishable": False,
        "sensitive": distribution == "trusted-beta",
        "profile_id": profile,
        "created_at": (datetime.now(UTC) - timedelta(days=days)).isoformat(),
        "build_id": build_id,
        "source": {"dirty": True},
        "toolchain": {},
        "installer": {"name": setup.name, "size": setup.stat().st_size, "sha256": sha256(setup)},
    }
    (folder / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def pair(retention):
    retention.configure(count=1, days=1)
    old, new = artifact(retention, 1), artifact(retention, 2, days=0)
    for p in (old, new):
        retention.register(p, apply=True)
    return old, new


def test_readonly_status_and_config_preview_do_not_initialize_control(retention):
    artifact(retention, 1)
    assert retention.inventory()["objects"][0]["reason"] == "unmanaged"
    assert not retention.configure(enabled=True, apply=False)["applied"]
    assert not retention.directory.exists()
    assert not retention.state()["enabled"]


def test_keep_union_and_history_requires_adoption(retention):
    folders = [artifact(retention, i, days=30 - i) for i in range(5)]
    for folder in folders:
        retention.register(folder, apply=True)
    recent = artifact(retention, "recent", days=1)
    retention.register(recent, apply=True)
    unknown = artifact(retention, "unknown", days=100)
    preview = retention.inventory()
    assert len(preview["candidates"]) == 3
    result = retention.collect(preview)
    assert len(result["deleted"]) == 3
    assert recent.exists() and unknown.exists()
    assert all(
        read_json(retention.journal_path(r["relative"]))["status"] == "completed"
        for r in result["deleted"]
    )


def test_pin_smoke_and_formal_are_never_candidates(retention):
    old, _ = pair(retention)
    smoke = artifact(retention, 3, profile="installer-smoke")
    retention.register(smoke, apply=True)
    retention.pin(old, "human acceptance")
    formal = retention.root / "releases/formal.exe"
    formal.parent.mkdir()
    formal.write_bytes(b"formal")
    assert not retention.inventory()["candidates"]
    assert not retention.collect(retention.inventory())["deleted"]
    assert formal.exists() and smoke.exists()


def test_plan_invalidated_by_pin_and_release_lock(retention):
    old, _ = pair(retention)
    preview = retention.inventory()
    retention.pin(old, "keep")
    with pytest.raises(Busy):
        retention.collect(preview)
    import subprocess
    import sys

    library = Path(__file__).resolve().parents[1] / "_lib"
    code = f"import sys;sys.path.insert(0,{str(library)!r});from pathlib import Path;from build_storage.locks import lock\nwith lock(Path({str(retention.lock_path)!r})): print('ready',flush=True);input()"
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(Busy):
            retention.collect(retention.inventory())
    finally:
        child.communicate("\n", timeout=10)
    assert old.exists()


@pytest.mark.parametrize("change", ["installer", "extra", "manifest"])
def test_changed_or_unknown_contents_cannot_be_deleted(retention, change):
    old, _ = pair(retention)
    preview = retention.inventory()
    if change == "installer":
        (old / "setup.exe").write_bytes(b"replaced")
    elif change == "extra":
        (old / "user-file.txt").write_text("keep")
    else:
        (old / "release.json").write_text("{}")
    result = retention.collect(preview)
    assert result["failed"] or result["skipped"]
    assert old.exists()


def test_partial_deletion_is_not_available_and_can_retry(retention, monkeypatch):
    old, _ = pair(retention)
    unlink = Path.unlink

    def denied(path, *a, **kw):
        if path == old / "release.json":
            raise PermissionError("locked manifest")
        return unlink(path, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(Path, "unlink", denied)
        result = retention.collect(retention.inventory())
    assert result["failed"] and not (old / "setup.exe").exists()
    assert any(r["reason"] == "deletion-failed" for r in retention.inventory()["objects"])
    retention.retry(old)
    assert not old.exists()


def test_evidence_failure_prevents_any_deletion(retention, monkeypatch):
    old, _ = pair(retention)

    def failed(*a):
        raise OSError("disk full")

    monkeypatch.setattr("release_retention.write_json", failed)
    assert retention.collect(retention.inventory())["failed"]
    assert (old / "setup.exe").exists()


def test_retry_rejects_new_unowned_content(retention, monkeypatch):
    old, _ = pair(retention)

    def failed(*a):
        raise OSError("interrupted")

    with monkeypatch.context() as m:
        m.setattr(retention, "_finish_delete", failed)
        assert retention.collect(retention.inventory())["failed"]
    (old / "important.txt").write_text("keep")
    with pytest.raises(ValueError, match="changed"):
        retention.retry(old)
    assert (old / "important.txt").exists()


def test_unrecognized_history_is_protected_without_blocking_valid_candidates(retention):
    old, new = pair(retention)
    legacy = retention.root / "_local-test/0.1.0/windows-x64/old-layout"
    legacy.mkdir(parents=True)
    (legacy / "keep.exe").write_bytes(b"unknown")
    preview = retention.inventory()
    assert not preview["complete"]
    result = retention.collect(preview)
    assert result["discoveryErrors"] and len(result["deleted"]) == 1
    assert not old.exists() and new.exists() and (legacy / "keep.exe").exists()


def test_unknown_subdirectory_inside_artifact_is_rejected(retention):
    old, _ = pair(retention)
    preview = retention.inventory()
    (old / "escape").mkdir()
    # Filesystem link traversal has its own platform test in build-storage; this verifies routing.
    result = retention.collect(preview)
    assert result["skipped"] and (old / "setup.exe").exists()


def test_artifact_junction_never_traversed_or_deleted(retention, tmp_path):
    import os
    import subprocess

    old, _ = pair(retention)
    preview = retention.inventory()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"user data")
    link = old / "escape"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
        )
        if result.returncode:
            pytest.skip("junction creation unavailable")
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        assert retention.collect(preview)["skipped"]
        assert (outside / "keep").read_bytes() == b"user data"
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_success_hook_registers_new_build_without_adopting_history(retention, monkeypatch):
    from types import SimpleNamespace

    import storage_governance as governance

    old = artifact(retention, "old")
    new = artifact(retention, "current", days=0)
    monkeypatch.setattr(governance, "Storage", lambda _: retention.storage)
    roots = SimpleNamespace(repo_root=retention.storage.repo, releases_root=retention.root)
    result = governance.maintain_releases(roots, new)
    assert not result["enabled"]
    rows = {r["path"]: r for r in retention.inventory()["objects"]}
    assert rows[str(old)]["reason"] == "unmanaged"
    assert rows[str(new)]["registered"]
    retention.configure(enabled=True, count=1, days=1)
    retention.register(old, apply=True)
    assert len(governance.maintain_releases(roots, new)["deleted"]) == 1


def test_finalize_uses_exact_published_build_identity(retention, monkeypatch):
    from types import SimpleNamespace

    import storage_governance as governance

    new = artifact(retention, "current", days=0)
    monkeypatch.setattr(governance, "Storage", lambda _: retention.storage)
    monkeypatch.setattr(governance, "complete_build", lambda *a, **kw: None)
    monkeypatch.setattr(governance, "maintain", lambda *a: None)
    paths = SimpleNamespace(
        roots=SimpleNamespace(repo_root=retention.storage.repo, releases_root=retention.root),
        build_id="build-current",
        build_dir=retention.root.parent / "work",
    )
    governance.finalize(paths, remove=True)
    assert retention.state()["artifacts"][new.relative_to(retention.root).as_posix()]
