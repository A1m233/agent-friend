from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import zipfile
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
PRODUCT_REPO = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_checked_in_builtin_snapshot_has_stable_lf_bytes() -> None:
    attribute = _git(
        PRODUCT_REPO,
        "check-attr",
        "eol",
        "--",
        "agent/src/agent/skills/builtin_assets/agent-friend-guide/SKILL.md",
    )

    assert attribute.endswith(": eol: lf")
    assert guide.builtin_state(PRODUCT_REPO)["valid"] is True


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


def test_prepare_checkout_clones_and_fast_forwards(tmp_path: Path) -> None:
    source, first = _init_guide_repo(tmp_path)
    origin = _git(source, "remote", "get-url", "origin")
    target = tmp_path / "managed" / "agent-friend-guide"

    cloned = guide.prepare_checkout(target, source_repository=origin)

    assert cloned["action"] == "cloned"
    assert cloned["head"] == first
    assert cloned["path"] == str(target.resolve())

    (source / "release-note.txt").write_text("next\n", encoding="utf-8")
    _git(source, "add", "release-note.txt")
    _git(source, "commit", "-m", "advance guide")
    _git(source, "push", "origin", "main")
    second = _git(source, "rev-parse", "HEAD")

    advanced = guide.prepare_checkout(target, source_repository=origin)

    assert advanced["action"] == "fast-forwarded"
    assert advanced["head"] == second


def test_prepare_checkout_rejects_dirty_or_wrong_origin(tmp_path: Path) -> None:
    source, _ = _init_guide_repo(tmp_path)
    origin = _git(source, "remote", "get-url", "origin")
    target = tmp_path / "managed"
    guide.prepare_checkout(target, source_repository=origin)
    (target / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(guide.GuideError, match="must be clean"):
        guide.prepare_checkout(target, source_repository=origin)

    (target / "dirty.txt").unlink()
    with pytest.raises(guide.GuideError, match="origin differs"):
        guide.prepare_checkout(target, source_repository="https://example.invalid/guide")


def test_prepare_checkout_rejects_detached_or_diverged(tmp_path: Path) -> None:
    source, _ = _init_guide_repo(tmp_path)
    origin = _git(source, "remote", "get-url", "origin")
    detached = tmp_path / "detached"
    guide.prepare_checkout(detached, source_repository=origin)
    _git(detached, "checkout", "--detach")

    with pytest.raises(guide.GuideError, match="detached HEAD"):
        guide.prepare_checkout(detached, source_repository=origin)

    diverged = tmp_path / "diverged"
    guide.prepare_checkout(diverged, source_repository=origin)
    _git(diverged, "config", "user.name", "Guide Test")
    _git(diverged, "config", "user.email", "guide@example.invalid")
    (diverged / "local.txt").write_text("local\n", encoding="utf-8")
    _git(diverged, "add", "local.txt")
    _git(diverged, "commit", "-m", "local divergence")

    with pytest.raises(guide.GuideError, match="cannot fast-forward"):
        guide.prepare_checkout(diverged, source_repository=origin)


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


def test_verify_package_allows_additional_references_scripts_and_assets(tmp_path: Path) -> None:
    repo = tmp_path / "guide"
    repo.mkdir()
    package = _write_package(repo)
    (package / "references" / "new-guide.md").write_text(
        "# New Guide\nAdditional first-party knowledge.\n",
        encoding="utf-8",
    )
    scripts = package / "scripts"
    scripts.mkdir()
    (scripts / "helper.py").write_text("print('not executed by validation')\n", encoding="utf-8")
    assets = package / "assets"
    assets.mkdir()
    (assets / "payload.bin").write_bytes(b"\x00\x01resource")
    source_path = package / "metadata" / "source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["content_sha256"] = guide.package_digest(package, exclude_source_metadata=True)
    source_path.write_text(json.dumps(source), encoding="utf-8")

    result = guide.verify_package(package)

    assert result["fileCount"] == 18


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


def test_build_release_assets_is_deterministic_and_contains_complete_package(
    tmp_path: Path,
) -> None:
    guide_repo, _commit = _init_guide_repo(tmp_path)
    package = guide.package_root(guide_repo)
    scripts = package / "scripts"
    scripts.mkdir()
    (scripts / "helper.py").write_text("print('resource only')\n", encoding="utf-8")
    source_path = package / "metadata" / "source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["content_sha256"] = guide.package_digest(package, exclude_source_metadata=True)
    source_path.write_text(json.dumps(source), encoding="utf-8")
    _git(guide_repo, "add", ".")
    _git(guide_repo, "commit", "-m", "add script resource")
    _git(guide_repo, "push", "origin", "main")
    commit = _git(guide_repo, "rev-parse", "HEAD")
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_result = guide.build_release_assets(
        guide_repo,
        release_sequence=3,
        min_product_version="0.2.0",
        max_product_version_exclusive="0.3.0",
        output_dir=first,
    )
    second_result = guide.build_release_assets(
        guide_repo,
        release_sequence=3,
        min_product_version="0.2.0",
        max_product_version_exclusive="0.3.0",
        output_dir=second,
    )

    first_zip = first / guide.RELEASE_PACKAGE_ASSET
    second_zip = second / guide.RELEASE_PACKAGE_ASSET
    assert first_zip.read_bytes() == second_zip.read_bytes()
    assert (first / guide.RELEASE_MANIFEST_ASSET).read_bytes() == (
        second / guide.RELEASE_MANIFEST_ASSET
    ).read_bytes()
    manifest = json.loads((first / guide.RELEASE_MANIFEST_ASSET).read_text(encoding="utf-8"))
    assert manifest["release_tag"] == "agent-friend-guide-r3"
    assert manifest["source_commit"] == commit
    assert manifest["package_sha256"] == first_result["packageSha256"]
    assert first_result["packageSha256"] == second_result["packageSha256"]
    with zipfile.ZipFile(first_zip) as bundle:
        assert "SKILL.md" in bundle.namelist()
        assert "scripts/helper.py" in bundle.namelist()


def test_build_release_assets_rejects_invalid_compatibility_range(tmp_path: Path) -> None:
    guide_repo, _commit = _init_guide_repo(tmp_path)

    with pytest.raises(guide.GuideError, match="non-empty"):
        guide.build_release_assets(
            guide_repo,
            release_sequence=1,
            min_product_version="0.3.0",
            max_product_version_exclusive="0.3.0",
            output_dir=tmp_path / "release",
        )


def test_publish_release_rejects_disabled_immutability_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str, **_kwargs: object) -> str:
        calls.append(args)
        return json.dumps({"enabled": False, "enforced_by_owner": False})

    monkeypatch.setattr(guide, "git", lambda *_args: guide.SOURCE_REPOSITORY)
    monkeypatch.setattr(guide, "gh", fake_gh)

    with pytest.raises(guide.GuideError, match="must be enabled"):
        guide.publish_release(
            Path("unused"),
            release_sequence=1,
            min_product_version="0.2.0",
            max_product_version_exclusive="0.3.0",
        )

    assert calls == [
        (
            "api",
            "repos/A1m233/agent-friend-guide/immutable-releases",
            "--header",
            "X-GitHub-Api-Version: 2026-03-10",
        )
    ]


def test_status_treats_builtin_as_optional_packaged_baseline(tmp_path: Path) -> None:
    guide_repo, commit = _init_guide_repo(tmp_path)
    product_repo = _init_product_repo(tmp_path)

    before = guide.status_payload(product_repo, guide_repo)
    guide.sync(product_repo, guide_repo, commit)
    after = guide.status_payload(product_repo, guide_repo)

    assert before["phase"] == "guide-commit-ready"
    assert after["phase"] == "guide-and-baseline-aligned"
    assert before["baselineMatchesGuide"] is False
    assert after["baselineMatchesGuide"] is True
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
