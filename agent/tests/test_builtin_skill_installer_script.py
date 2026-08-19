from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from agent.skills import SkillCatalogService, SkillSelection


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


def _fixture_archive(
    *,
    name: str = "fixture-skill",
    description: str = "Deterministic install fixture.",
    script: str = "print('fixture')\n",
    include_skill_md: bool = True,
) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        if include_skill_md:
            archive.writestr(
                "fixture-main/skills/fixture-skill/SKILL.md",
                f"---\nname: {name}\ndescription: {description}\n---\n\n{description}\n",
            )
        archive.writestr(
            "fixture-main/skills/fixture-skill/scripts/run.py",
            script,
        )
    return payload.getvalue()


def _install_fixture(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    personal: Path,
    downloads: Path,
    payload: bytes,
) -> int:
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(personal))
    monkeypatch.setattr(module, "_request", lambda _url: payload)
    monkeypatch.setattr(module, "_tmp_root", lambda: str(downloads))
    return cast(
        int,
        module.main(
            [
                "--repo",
                "fixture/repo",
                "--path",
                "skills/fixture-skill",
                "--method",
                "download",
            ]
        ),
    )


def test_installer_downloads_into_host_personal_root_and_catalog_discovers_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    result = _install_fixture(module, monkeypatch, personal, downloads, _fixture_archive())

    assert result == 0
    installed = personal / "fixture-skill"
    assert (installed / "SKILL.md").is_file()
    assert (installed / "scripts" / "run.py").is_file()
    lock = json.loads((personal / ".agent-friend-skill-lock.json").read_text("utf-8"))
    record = lock["skills"]["fixture-skill"]
    assert record["repository"] == "fixture/repo"
    assert record["path"] == "skills/fixture-skill"
    assert record["ref"] == "main"
    assert record["content_sha256"] == module.package_digest(installed)

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


def test_managed_update_changes_content_and_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    assert (
        _install_fixture(module, monkeypatch, personal, downloads, _fixture_archive(script="v1\n"))
        == 0
    )
    capsys.readouterr()
    monkeypatch.setattr(module, "_request", lambda _url: _fixture_archive(script="v2\n"))

    result = module.main(
        ["--update", "fixture-skill", "--ref", "release-v2", "--method", "download"]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Updated fixture-skill" in captured.out
    assert "old_sha256=" in captured.out and "new_sha256=" in captured.out
    assert (personal / "fixture-skill" / "scripts" / "run.py").read_text("utf-8") == "v2\n"
    record = json.loads((personal / ".agent-friend-skill-lock.json").read_text("utf-8"))["skills"][
        "fixture-skill"
    ]
    assert record["ref"] == "release-v2"
    assert record["content_sha256"] == module.package_digest(personal / "fixture-skill")


def test_idempotent_update_does_not_rewrite_skill_or_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    payload = _fixture_archive()
    assert _install_fixture(module, monkeypatch, personal, downloads, payload) == 0
    capsys.readouterr()
    entry = personal / "fixture-skill" / "SKILL.md"
    lock = personal / ".agent-friend-skill-lock.json"
    before = (entry.stat().st_mtime_ns, lock.stat().st_mtime_ns, lock.read_bytes())

    result = module.main(
        [
            "--update",
            "fixture-skill",
            "--ref",
            "same-content-tag",
            "--method",
            "download",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Already up to date: fixture-skill" in captured.out
    assert "@main" in captured.out
    assert (entry.stat().st_mtime_ns, lock.stat().st_mtime_ns, lock.read_bytes()) == before


def test_local_changes_require_explicit_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    assert _install_fixture(module, monkeypatch, personal, downloads, _fixture_archive()) == 0
    capsys.readouterr()
    script = personal / "fixture-skill" / "scripts" / "run.py"
    script.write_text("local edit\n", encoding="utf-8")
    before_lock = (personal / ".agent-friend-skill-lock.json").read_bytes()
    monkeypatch.setattr(module, "_request", lambda _url: _fixture_archive(script="upstream\n"))

    blocked = module.main(["--update", "fixture-skill", "--method", "download"])
    blocked_output = capsys.readouterr()

    assert blocked == 1
    assert "Error [local_changes_detected]" in blocked_output.err
    assert "--update fixture-skill --replace-local-changes" in blocked_output.err
    assert script.read_text("utf-8") == "local edit\n"
    assert (personal / ".agent-friend-skill-lock.json").read_bytes() == before_lock

    replaced = module.main(
        [
            "--update",
            "fixture-skill",
            "--replace-local-changes",
            "--method",
            "download",
        ]
    )

    assert replaced == 0
    assert script.read_text("utf-8") == "upstream\n"


def test_unmanaged_skill_requires_confirmation_before_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    installed = personal / "fixture-skill"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text(
        "---\nname: fixture-skill\ndescription: Existing unmanaged Skill.\n---\n",
        encoding="utf-8",
    )
    (installed / "local.txt").write_text("keep until confirmed\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(personal))
    monkeypatch.setattr(module, "_tmp_root", lambda: str(downloads))
    monkeypatch.setattr(module, "_request", lambda _url: _fixture_archive(script="adopted\n"))
    command = [
        "--repo",
        "fixture/repo",
        "--path",
        "skills/fixture-skill",
        "--name",
        "fixture-skill",
        "--method",
        "download",
    ]

    blocked = module.main(command)
    blocked_output = capsys.readouterr()

    assert blocked == 1
    assert "Error [unmanaged_destination_exists]" in blocked_output.err
    assert (installed / "local.txt").is_file()
    assert not (personal / ".agent-friend-skill-lock.json").exists()

    adopted = module.main([*command, "--replace-unmanaged"])

    assert adopted == 0
    assert not (installed / "local.txt").exists()
    assert (installed / "scripts" / "run.py").read_text("utf-8") == "adopted\n"
    assert (
        "fixture-skill"
        in json.loads((personal / ".agent-friend-skill-lock.json").read_text("utf-8"))["skills"]
    )


@pytest.mark.parametrize(
    ("repository", "source_path"),
    [
        ("other/repo", "skills/fixture-skill"),
        ("fixture/repo", "other/fixture-skill"),
    ],
)
def test_different_source_cannot_replace_managed_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repository: str,
    source_path: str,
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    assert _install_fixture(module, monkeypatch, personal, downloads, _fixture_archive()) == 0
    capsys.readouterr()
    before = (personal / "fixture-skill" / "SKILL.md").read_bytes()
    before_lock = (personal / ".agent-friend-skill-lock.json").read_bytes()

    result = module.main(
        [
            "--repo",
            repository,
            "--path",
            source_path,
            "--method",
            "download",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Error [source_conflict]" in captured.err
    assert (personal / "fixture-skill" / "SKILL.md").read_bytes() == before
    assert (personal / ".agent-friend-skill-lock.json").read_bytes() == before_lock


@pytest.mark.parametrize(
    "invalid_payload",
    [
        _fixture_archive(include_skill_md=False),
        _fixture_archive(name="different-name"),
    ],
)
def test_invalid_staging_keeps_previous_skill_and_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid_payload: bytes,
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    assert _install_fixture(module, monkeypatch, personal, downloads, _fixture_archive()) == 0
    capsys.readouterr()
    before_entry = (personal / "fixture-skill" / "SKILL.md").read_bytes()
    before_lock = (personal / ".agent-friend-skill-lock.json").read_bytes()
    monkeypatch.setattr(module, "_request", lambda _url: invalid_payload)

    result = module.main(["--update", "fixture-skill", "--method", "download"])

    assert result == 1
    assert (personal / "fixture-skill" / "SKILL.md").read_bytes() == before_entry
    assert (personal / ".agent-friend-skill-lock.json").read_bytes() == before_lock


def test_lock_write_failure_rolls_back_previous_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    assert (
        _install_fixture(module, monkeypatch, personal, downloads, _fixture_archive(script="v1\n"))
        == 0
    )
    capsys.readouterr()
    before_lock = (personal / ".agent-friend-skill-lock.json").read_bytes()
    monkeypatch.setattr(module, "_request", lambda _url: _fixture_archive(script="v2\n"))
    managed = sys.modules[module.commit_candidate.__module__]

    def fail_lock_write(_root: Path, _records: object) -> None:
        raise managed.ManagedInstallError("lock_write_failed", "injected lock failure")

    monkeypatch.setattr(managed, "_write_records", fail_lock_write)

    result = module.main(["--update", "fixture-skill", "--method", "download"])

    captured = capsys.readouterr()
    assert result == 1
    assert "Error [lock_write_failed]" in captured.err
    assert (personal / "fixture-skill" / "scripts" / "run.py").read_text("utf-8") == "v1\n"
    assert (personal / ".agent-friend-skill-lock.json").read_bytes() == before_lock


def test_first_install_lock_failure_leaves_no_unmanaged_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(personal))
    monkeypatch.setattr(module, "_tmp_root", lambda: str(downloads))
    monkeypatch.setattr(module, "_request", lambda _url: _fixture_archive())
    managed = sys.modules[module.commit_candidate.__module__]

    def fail_lock_write(_root: Path, _records: object) -> None:
        raise managed.ManagedInstallError("lock_write_failed", "injected lock failure")

    monkeypatch.setattr(managed, "_write_records", fail_lock_write)

    result = module.main(
        ["--repo", "fixture/repo", "--path", "skills/fixture-skill", "--method", "download"]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Error [lock_write_failed]" in captured.err
    assert not (personal / "fixture-skill").exists()
    assert not (personal / ".agent-friend-skill-lock.json").exists()


def test_final_recheck_rejects_content_changed_while_candidate_was_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    assert _install_fixture(module, monkeypatch, personal, downloads, _fixture_archive()) == 0
    managed = sys.modules[module.commit_candidate.__module__]
    record = managed.load_records(personal)["fixture-skill"]
    current_sha256 = managed.package_digest(personal / "fixture-skill")
    source = tmp_path / "replacement"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: fixture-skill\ndescription: Concurrent replacement.\n---\n",
        encoding="utf-8",
    )
    candidate, candidate_sha256 = managed.stage_candidate(source, personal, "fixture-skill")
    next_record = managed.make_record(
        repository=record.repository,
        path=record.path,
        ref=record.ref,
        content_sha256=candidate_sha256,
    )
    (personal / "fixture-skill" / "concurrent.txt").write_text(
        "changed after staging\n", encoding="utf-8"
    )

    try:
        with pytest.raises(managed.ManagedInstallError) as raised:
            managed.commit_candidate(
                root=personal,
                skill_name="fixture-skill",
                candidate=candidate,
                record=next_record,
                expected_mode="managed",
                expected_record=record,
                expected_current_sha256=current_sha256,
            )
        assert raised.value.code == "concurrent_update"
        assert (personal / "fixture-skill" / "concurrent.txt").is_file()
        assert managed.load_records(personal)["fixture-skill"] == record
    finally:
        managed.remove_candidate(candidate)


def test_candidate_link_is_rejected_before_destination_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer()
    managed = sys.modules[module.commit_candidate.__module__]
    source = tmp_path / "source"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: fixture-skill\ndescription: Unsafe link fixture.\n---\n",
        encoding="utf-8",
    )
    suspicious = scripts / "run.py"
    suspicious.write_text("print('unsafe')\n", encoding="utf-8")
    original_is_link = managed._is_link
    monkeypatch.setattr(
        managed,
        "_is_link",
        lambda path: path == suspicious or original_is_link(path),
    )

    with pytest.raises(managed.ManagedInstallError) as raised:
        managed.stage_candidate(source, tmp_path / "personal", "fixture-skill")

    assert raised.value.code == "unsafe_package"
    assert not (tmp_path / "personal" / "fixture-skill").exists()


def test_conservative_validator_acceptance_is_loadable_by_production_catalog(
    tmp_path: Path,
) -> None:
    module = _load_installer()
    managed = sys.modules[module.commit_candidate.__module__]
    source = tmp_path / "source"
    agents = source / "agents"
    agents.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\n"
        "name: fixture-skill\n"
        "description: >-\n"
        "  A folded description accepted by the conservative validator.\n"
        "metadata:\n"
        "  version: '1.0'\n"
        "---\n\n"
        "Follow the fixture workflow.\n",
        encoding="utf-8",
    )
    (agents / "openai.yaml").write_text(
        "interface:\n"
        '  display_name: "Fixture Skill"\n'
        "policy:\n"
        "  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )
    personal = tmp_path / "personal"
    candidate, digest = managed.stage_candidate(source, personal, "fixture-skill")
    record = managed.make_record(
        repository="fixture/repo",
        path="skills/fixture-skill",
        ref="main",
        content_sha256=digest,
    )
    managed.commit_candidate(
        root=personal,
        skill_name="fixture-skill",
        candidate=candidate,
        record=record,
        expected_mode="absent",
        expected_record=None,
        expected_current_sha256=None,
    )

    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = service.initialize()
    loaded = next(item for item in snapshot.entries if item.name == "fixture-skill")

    assert loaded.description == "A folded description accepted by the conservative validator."
    assert loaded.allow_implicit_invocation is False
    assert not snapshot.issues


def test_invalid_lock_fails_closed_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    personal.mkdir()
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (personal / ".agent-friend-skill-lock.json").write_bytes(
        b'\xef\xbb\xbf{"schema_version": 1, "skills": {}}'
    )
    monkeypatch.setenv("AGENT_FRIEND_PERSONAL_SKILLS_DIR", str(personal))
    monkeypatch.setattr(module, "_tmp_root", lambda: str(downloads))
    requested = False

    def request(_url: str) -> bytes:
        nonlocal requested
        requested = True
        return _fixture_archive()

    monkeypatch.setattr(module, "_request", request)

    result = module.main(
        ["--repo", "fixture/repo", "--path", "skills/fixture-skill", "--method", "download"]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Error [invalid_lock]" in captured.err
    assert not requested
    assert not (personal / "fixture-skill").exists()


def test_successful_update_refreshes_later_turn_while_frozen_turn_keeps_old_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer()
    personal = tmp_path / "personal"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    assert (
        _install_fixture(
            module,
            monkeypatch,
            personal,
            downloads,
            _fixture_archive(description="Version one", script="v1\n"),
        )
        == 0
    )
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=True,
    )
    service.start()
    try:
        initial = next(item for item in service.snapshot.entries if item.name == "fixture-skill")
        frozen = service.begin_turn((SkillSelection(initial.id),), model_window=100_000)
        monkeypatch.setattr(
            module,
            "_request",
            lambda _url: _fixture_archive(description="Version two", script="v2\n"),
        )

        assert module.main(["--update", "fixture-skill", "--method", "download"]) == 0
        _wait_until(
            lambda: any(
                item.name == "fixture-skill" and item.description == "Version two"
                for item in service.snapshot.entries
            )
        )
        later = service.begin_turn((SkillSelection(initial.id),), model_window=100_000)
    finally:
        service.stop()

    assert "Version one" in frozen.activations[0].contents
    assert "Version two" in later.activations[0].contents
    assert frozen.activations[0].version != later.activations[0].version


def _wait_until(predicate: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not met before timeout")
