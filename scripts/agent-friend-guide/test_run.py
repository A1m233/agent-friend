from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    path = Path(__file__).with_name("run.py")
    spec = importlib.util.spec_from_file_location("agent_friend_guide_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guide = _load_script()


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _write_package(repo: Path) -> Path:
    package = repo / "skills" / "agent-friend-guide"
    references = package / "references"
    agents = package / "agents"
    metadata = package / "metadata"
    references.mkdir(parents=True)
    agents.mkdir()
    metadata.mkdir()
    route_lines = "\n".join(
        f"- `{f'references/{name}'}`" for name in sorted(guide.ROUTED_REFERENCES)
    )
    (package / "SKILL.md").write_text(
        "---\n"
        "name: agent-friend-guide\n"
        "description: Test agent-friend user knowledge.\n"
        "---\n\n"
        f"{route_lines}\n\n"
        "`references/getting-started.md` is a placeholder.\n",
        encoding="utf-8",
    )
    (agents / "openai.yaml").write_text(
        "interface:\n  display_name: Guide\npolicy:\n  allow_implicit_invocation: true\n",
        encoding="utf-8",
    )
    for name in sorted(guide.EXPECTED_REFERENCES):
        terms = guide.REQUIRED_CONTENT.get(name, ())
        body = "\n".join((f"# {name}", *terms, "User-facing content."))
        (references / name).write_text(body + "\n", encoding="utf-8")
    (package / "license.txt").write_text("Apache-2.0\n", encoding="utf-8")
    source = {
        "schema_version": 1,
        "product_repository": "https://github.com/A1m233/agent-friend",
        "reviewed_product_commit": "1" * 40,
        "reviewed_at": "2026-08-09",
        "product_version": "0.1.7",
        "content_sha256": "0" * 64,
    }
    source_path = metadata / "source.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    source["content_sha256"] = guide.package_digest(package, exclude_source_metadata=True)
    source_path.write_text(json.dumps(source), encoding="utf-8")
    return package


def _init_guide_repo(tmp_path: Path, *, publish: bool = True) -> tuple[Path, str]:
    repo = tmp_path / "guide"
    repo.mkdir()
    _write_package(repo)
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "Guide Test")
    _git(repo, "config", "user.email", "guide@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add guide")
    commit = _git(repo, "rev-parse", "HEAD")
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        capture_output=True,
    )
    _git(repo, "remote", "add", "origin", str(origin))
    if publish:
        _git(repo, "push", "-u", "origin", "main")
    return repo, commit


def _init_product_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "product"
    builtin = repo / "agent" / "src" / "agent" / "skills" / "builtin_assets"
    creator = builtin / "skill-creator"
    creator.mkdir(parents=True)
    (creator / "SKILL.md").write_text("creator\n", encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "skills": {
            "skill-creator": {
                "source_repository": "https://example.invalid/creator",
                "source_commit": "2" * 40,
                "source_path": "skill-creator",
                "license": "Apache-2.0",
                "snapshot_sha256": "3" * 64,
            }
        },
    }
    (builtin / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "Product Test")
    _git(repo, "config", "user.email", "product@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add product")
    return repo


def test_verify_package_checks_routes_metadata_and_provider_sections(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "guide"
    repo.mkdir()
    package = _write_package(repo)

    result = guide.verify_package(package)

    assert result["fileCount"] == 15
    assert result["source"]["reviewed_product_commit"] == "1" * 40
    assert len(result["snapshotSha256"]) == 64


def test_verify_package_rejects_digest_drift(tmp_path: Path) -> None:
    repo = tmp_path / "guide"
    repo.mkdir()
    package = _write_package(repo)
    weather = package / "references" / "weather-credentials.md"
    weather.write_text(
        weather.read_text(encoding="utf-8") + "changed after metadata\n",
        encoding="utf-8",
    )

    with pytest.raises(guide.GuideError, match="content_sha256 mismatch"):
        guide.verify_package(package)


def test_verify_package_rejects_linked_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "guide"
    repo.mkdir()
    package = _write_package(repo)
    linked = package / "references" / "weather-credentials.md"
    original = guide.is_link
    monkeypatch.setattr(guide, "is_link", lambda path: path == linked or original(path))

    with pytest.raises(guide.GuideError, match="links are not allowed"):
        guide.verify_package(package)


def test_sync_requires_published_clean_exact_commit(tmp_path: Path) -> None:
    guide_repo, commit = _init_guide_repo(tmp_path, publish=False)
    product_repo = _init_product_repo(tmp_path)

    with pytest.raises(guide.GuideError, match="not the exact target"):
        guide.sync(product_repo, guide_repo, commit)

    _git(guide_repo, "push", "-u", "origin", "main")
    (guide_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(guide.GuideError, match="must be clean"):
        guide.sync(product_repo, guide_repo, commit)


def test_sync_copies_exact_snapshot_and_updates_manifest(tmp_path: Path) -> None:
    guide_repo, commit = _init_guide_repo(tmp_path)
    product_repo = _init_product_repo(tmp_path)
    public = guide.verify_package(guide.package_root(guide_repo))

    result = guide.sync(product_repo, guide_repo, commit)

    assert result["guideCommit"] == commit
    assert result["snapshotSha256"] == public["snapshotSha256"]
    state = guide.builtin_state(product_repo)
    assert state["valid"] is True
    assert state["sourceCommit"] == commit
    assert state["snapshotSha256"] == public["snapshotSha256"]
    manifest = guide.load_manifest(product_repo)
    assert manifest["skills"]["skill-creator"]["source_commit"] == "2" * 40


def test_status_reports_published_then_synchronized_phase(tmp_path: Path) -> None:
    guide_repo, commit = _init_guide_repo(tmp_path)
    product_repo = _init_product_repo(tmp_path)

    before = guide.status_payload(product_repo, guide_repo)
    guide.sync(product_repo, guide_repo, commit)
    after = guide.status_payload(product_repo, guide_repo)

    assert before["phase"] == "guide-published-builtin-missing"
    assert after["phase"] == "guide-and-builtin-synchronized"
    assert after["matches"] is True


def test_snapshot_replace_restores_previous_copy_when_manifest_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guide_repo = tmp_path / "guide"
    guide_repo.mkdir()
    source = _write_package(guide_repo)
    product_repo = _init_product_repo(tmp_path)
    root = guide.builtin_root(product_repo)
    target = root / guide.SKILL_NAME
    target.mkdir()
    (target / "old.txt").write_text("old\n", encoding="utf-8")
    original_manifest = (root / "manifest.json").read_bytes()
    replacement_manifest = guide.updated_manifest(
        guide.load_manifest(product_repo),
        guide_commit="4" * 40,
        snapshot_sha256="5" * 64,
    )
    real_replace = os.replace

    def fail_manifest_swap(source_path: os.PathLike[str], target_path: os.PathLike[str]) -> None:
        if Path(target_path) == root / "manifest.json":
            raise OSError("simulated manifest swap failure")
        real_replace(source_path, target_path)

    monkeypatch.setattr(guide.os, "replace", fail_manifest_swap)

    with pytest.raises(OSError, match="simulated"):
        guide.replace_snapshot(product_repo, source, replacement_manifest)

    assert (target / "old.txt").read_text(encoding="utf-8") == "old\n"
    assert (root / "manifest.json").read_bytes() == original_manifest
    assert not list(root.glob(".agent-friend-guide.*"))


def test_fixture_copy_has_no_links(tmp_path: Path) -> None:
    repo = tmp_path / "guide"
    repo.mkdir()
    package = _write_package(repo)
    copy = tmp_path / "copy"

    shutil.copytree(package, copy)

    assert all(not guide.is_link(path) for path in copy.rglob("*"))
