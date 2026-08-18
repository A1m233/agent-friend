from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
from agent.skills import SkillCatalogService


def _load_installer() -> ModuleType:
    scripts = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent"
        / "skills"
        / "builtin_assets"
        / "skill-installer"
        / "scripts"
    )
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location(
            "agent_friend_builtin_skill_installer_test",
            scripts / "install-skill-from-github.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts))


def _fixture_archive() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "fixture-main/skills/fixture-skill/SKILL.md",
            "---\nname: fixture-skill\ndescription: Deterministic install fixture.\n---\n",
        )
        archive.writestr(
            "fixture-main/skills/fixture-skill/scripts/run.py",
            "print('fixture')\n",
        )
    return payload.getvalue()


def test_installer_downloads_into_host_personal_root_and_catalog_discovers_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(personal))
    monkeypatch.setattr(module, "_request", lambda _url: _fixture_archive())
    monkeypatch.setattr(module, "_tmp_root", lambda: str(downloads))

    result = module.main(
        [
            "--repo",
            "fixture/repo",
            "--path",
            "skills/fixture-skill",
            "--method",
            "download",
        ]
    )

    assert result == 0
    installed = personal / "fixture-skill"
    assert (installed / "SKILL.md").is_file()
    assert (installed / "scripts" / "run.py").is_file()

    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = service.initialize()
    assert any(
        item.source == "personal" and item.package_root == installed for item in snapshot.entries
    )


@pytest.mark.parametrize("http_status", [401, 403, 404, 429, 503])
def test_auto_mode_falls_back_to_git_when_github_download_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_status: int
) -> None:
    module = _load_installer()
    expected = str(tmp_path / "git-checkout")
    source = module.Source(
        owner="fixture",
        repo="repo",
        ref="main",
        paths=["skills/fixture-skill"],
    )

    def download_failed(*_args: object) -> str:
        raise module.InstallError(f"Download failed: HTTP {http_status}")

    monkeypatch.setattr(module, "_download_repo_zip", download_failed)
    monkeypatch.setattr(module, "_git_sparse_checkout", lambda *_args: expected)

    assert module._prepare_repo(source, "auto", str(tmp_path)) == expected
