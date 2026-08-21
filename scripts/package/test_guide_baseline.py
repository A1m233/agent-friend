from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import guide_baseline
import pytest

GUIDE_COMMIT = "4" * 40
SNAPSHOT_SHA256 = "5" * 64


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _product_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "product"
    target = repo / guide_baseline.GUIDE_RELATIVE_PATH
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("old baseline\n", encoding="utf-8")
    manifest = repo / guide_baseline.MANIFEST_RELATIVE_PATH
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "skills": {
                    "agent-friend-guide": {
                        "source_commit": "1" * 40,
                        "snapshot_sha256": "2" * 64,
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "unrelated.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.name", "Package Test")
    _git(repo, "config", "user.email", "package-test@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    return repo


class _FakeGuide:
    @staticmethod
    def git_changes(_repo: Path) -> list[str]:
        return []

    @staticmethod
    def git_head(_repo: Path) -> str:
        return GUIDE_COMMIT

    @staticmethod
    def origin_contains_exact_ref(_repo: Path, commit: str) -> bool:
        return commit == GUIDE_COMMIT

    @staticmethod
    def package_root(repo: Path) -> Path:
        return repo / "skills" / "agent-friend-guide"

    @staticmethod
    def verify_package(_root: Path) -> dict[str, str]:
        return {"snapshotSha256": SNAPSHOT_SHA256}

    @staticmethod
    def builtin_state(product_repo: Path) -> dict[str, object]:
        manifest = json.loads(
            (product_repo / guide_baseline.MANIFEST_RELATIVE_PATH).read_text("utf-8")
        )
        value = manifest["skills"]["agent-friend-guide"]
        return {
            "valid": True,
            "sourceCommit": value["source_commit"],
            "snapshotSha256": value["snapshot_sha256"],
        }

    @staticmethod
    def sync(product_repo: Path, _guide_repo: Path, commit: str) -> dict[str, str]:
        target = product_repo / guide_baseline.GUIDE_RELATIVE_PATH
        shutil.rmtree(target)
        target.mkdir()
        (target / "SKILL.md").write_text("package baseline\n", encoding="utf-8")
        manifest = product_repo / guide_baseline.MANIFEST_RELATIVE_PATH
        value = json.loads(manifest.read_text("utf-8"))
        value["skills"]["agent-friend-guide"] = {
            "source_commit": commit,
            "snapshot_sha256": SNAPSHOT_SHA256,
        }
        manifest.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return {"snapshotSha256": SNAPSHOT_SHA256}

    @staticmethod
    def replace_snapshot(
        product_repo: Path, source: Path, manifest_value: dict[str, object]
    ) -> None:
        target = product_repo / guide_baseline.GUIDE_RELATIVE_PATH
        shutil.rmtree(target)
        shutil.copytree(source, target)
        (product_repo / guide_baseline.MANIFEST_RELATIVE_PATH).write_text(
            json.dumps(manifest_value, indent=2) + "\n",
            encoding="utf-8",
        )


def _use_fake_guide(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guide_baseline, "_load_guide_module", lambda _repo: _FakeGuide)


def test_transient_baseline_restores_entry_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _product_repo(tmp_path)
    guide_repo = tmp_path / "guide"
    guide_repo.mkdir()
    (repo / "unrelated.txt").write_text("pre-existing dirty\n", encoding="utf-8")
    entry_status = guide_baseline._git_status(repo)
    _use_fake_guide(monkeypatch)

    with guide_baseline.transient_baseline(repo, guide_repo, GUIDE_COMMIT) as active:
        assert active.lifecycle == "transient"
        assert active.guide_commit == GUIDE_COMMIT
        assert active.snapshot_sha256 == SNAPSHOT_SHA256
        assert (repo / guide_baseline.GUIDE_RELATIVE_PATH / "SKILL.md").read_text(
            "utf-8"
        ) == "package baseline\n"
        assert guide_baseline.inherited_baseline(repo) is not None

    assert guide_baseline._git_status(repo) == entry_status
    assert (repo / guide_baseline.GUIDE_RELATIVE_PATH / "SKILL.md").read_text(
        "utf-8"
    ) == "old baseline\n"
    assert (repo / "unrelated.txt").read_text("utf-8") == "pre-existing dirty\n"
    assert guide_baseline.inherited_baseline(repo) is None


def test_transient_baseline_restores_after_build_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _product_repo(tmp_path)
    guide_repo = tmp_path / "guide"
    guide_repo.mkdir()
    entry_status = guide_baseline._git_status(repo)
    _use_fake_guide(monkeypatch)

    with (
        pytest.raises(RuntimeError, match="build failed"),
        guide_baseline.transient_baseline(repo, guide_repo, GUIDE_COMMIT),
    ):
        raise RuntimeError("build failed")

    assert guide_baseline._git_status(repo) == entry_status
    assert (repo / guide_baseline.GUIDE_RELATIVE_PATH / "SKILL.md").read_text(
        "utf-8"
    ) == "old baseline\n"


def test_transient_baseline_rejects_preexisting_managed_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _product_repo(tmp_path)
    guide_repo = tmp_path / "guide"
    guide_repo.mkdir()
    target = repo / guide_baseline.GUIDE_RELATIVE_PATH / "SKILL.md"
    target.write_text("user change\n", encoding="utf-8")
    entry_status = guide_baseline._git_status(repo)
    _use_fake_guide(monkeypatch)

    with (
        pytest.raises(RuntimeError, match="already dirty"),
        guide_baseline.transient_baseline(repo, guide_repo, GUIDE_COMMIT),
    ):
        raise AssertionError("unreachable")

    assert guide_baseline._git_status(repo) == entry_status
    assert target.read_text("utf-8") == "user change\n"


def test_formal_baseline_mismatch_fails_without_mutating_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _product_repo(tmp_path)
    guide_repo = tmp_path / "guide"
    guide_repo.mkdir()
    entry_status = guide_baseline._git_status(repo)
    _use_fake_guide(monkeypatch)

    with pytest.raises(RuntimeError, match="baseline-update-required"):
        guide_baseline.require_committed_baseline(repo, guide_repo, GUIDE_COMMIT)

    assert guide_baseline._git_status(repo) == entry_status


def test_formal_baseline_accepts_exact_committed_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _product_repo(tmp_path)
    guide_repo = tmp_path / "guide"
    guide_repo.mkdir()
    _use_fake_guide(monkeypatch)
    _FakeGuide.sync(repo, guide_repo, GUIDE_COMMIT)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "update baseline")

    active = guide_baseline.require_committed_baseline(repo, guide_repo, GUIDE_COMMIT)

    assert active.lifecycle == "committed"
    assert active.guide_commit == GUIDE_COMMIT
    assert active.snapshot_sha256 == SNAPSHOT_SHA256
