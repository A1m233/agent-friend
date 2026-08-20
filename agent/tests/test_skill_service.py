from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from agent.filesystem import FILESYSTEM_TOOL_NAMES
from agent.skills import (
    SkillCatalogService,
    SkillSelection,
    render_activated_skill,
    render_runtime_capability_notice,
)
from agent.skills.builtin import BuiltinSkillInstaller


def _write_skill(root: Path, name: str, body: str = "Follow this workflow.") -> Path:
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    entry = package / "SKILL.md"
    entry.write_text(
        f"---\nname: {name}\ndescription: Test {name}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return entry


def _package_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_service_installs_builtin_and_activates_three_sources(tmp_path: Path) -> None:
    personal = tmp_path / "personal"
    shared = tmp_path / "shared"
    _write_skill(personal, "personal-skill")
    _write_skill(shared, "shared-skill")
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=shared,
        watcher_enabled=False,
    )

    snapshot = service.initialize()

    assert [(item.source, item.name) for item in snapshot.entries] == [
        ("builtin", "agent-friend-guide"),
        ("builtin", "skill-creator"),
        ("builtin", "skill-installer"),
        ("personal", "personal-skill"),
        ("shared", "shared-skill"),
    ]
    by_name = {item.name: item for item in snapshot.entries}
    guide = by_name["agent-friend-guide"]
    creator = by_name["skill-creator"]
    installer = by_name["skill-installer"]
    assert guide.id == "builtin/agent-friend-guide"
    assert creator.id == "builtin/skill-creator"
    assert installer.id == "builtin/skill-installer"
    manifest = json.loads((creator.package_root.parent / "manifest.json").read_text("utf-8"))
    assert manifest["schema_version"] == 2
    assert (
        manifest["skills"]["skill-creator"]["source_commit"]
        == "9d00bb01c0a712fb7c2f5b002bdf33bcc0fc352c"
    )
    assert (
        manifest["skills"]["agent-friend-guide"]["source_commit"]
        == "6a2958743aeec75a5047147648f1f6909f2e4848"
    )
    assert (
        manifest["skills"]["skill-installer"]["source_commit"]
        == "9ded177ce7c1c0bd2047f902936c177612ab3434"
    )
    assert (creator.package_root / "license.txt").is_file()
    assert (creator.package_root / "scripts" / "quick_validate.py").is_file()
    assert (installer.package_root / "scripts" / "install-skill-from-github.py").is_file()
    assert (installer.package_root / "scripts" / "managed_install.py").is_file()
    assert manifest["skills"]["skill-installer"]["snapshot_sha256"] == _package_digest(
        installer.package_root
    )
    builtin_text = creator.entry_path.read_text("utf-8")
    assert "cannot yet read bundled references" not in builtin_text
    assert "host-provided `<runtime_capabilities>`" in builtin_text
    assert (guide.package_root / "references" / "voice-credentials.md").is_file()
    assert "语音输入" in (guide.package_root / "references" / "voice-credentials.md").read_text(
        "utf-8"
    )

    turn = service.begin_turn(
        tuple(SkillSelection(item.id, item.version) for item in snapshot.entries),
        model_window=100_000,
    )
    assert [item.record.name for item in turn.activations] == [
        "agent-friend-guide",
        "skill-creator",
        "skill-installer",
        "personal-skill",
        "shared-skill",
    ]
    assert not turn.activation_issues


def test_builtin_installer_rejects_unexpected_files_in_existing_bundle(
    tmp_path: Path,
) -> None:
    installer = BuiltinSkillInstaller(target_root=tmp_path / "skills")
    installed = installer.install()
    (installed / "unexpected.txt").write_text("drift\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="verification failed"):
        installer.install()


def test_service_refreshes_changed_skill_without_reusing_stale_body(tmp_path: Path) -> None:
    personal = tmp_path / "personal"
    entry = _write_skill(personal, "changing-skill", "version one")
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    initial = service.initialize()
    record = next(item for item in initial.entries if item.name == "changing-skill")
    entry.write_text(
        "---\nname: changing-skill\ndescription: Test changing\n---\n\nversion two\n",
        encoding="utf-8",
    )

    turn = service.begin_turn((SkillSelection(record.id),), model_window=100_000)

    assert turn.catalog.generation == initial.generation + 1
    assert turn.activations[0].contents.replace("\r\n", "\n").endswith("version two\n")
    assert turn.activations[0].version != record.version


def test_watcher_adds_and_removes_skill_without_restart(tmp_path: Path) -> None:
    personal = tmp_path / "personal"
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=True,
    )
    service.start()
    try:
        entry = _write_skill(personal, "live-skill")
        _wait_until(lambda: any(item.name == "live-skill" for item in service.snapshot.entries))
        initial = next(item for item in service.snapshot.entries if item.name == "live-skill")
        replacement = entry.with_name("SKILL.md.tmp")
        replacement.write_text(
            "---\nname: live-skill\ndescription: Updated metadata\n---\n\nupdated body\n",
            encoding="utf-8",
        )
        replacement.replace(entry)
        _wait_until(
            lambda: any(
                item.name == "live-skill"
                and item.description == "Updated metadata"
                and item.version != initial.version
                for item in service.snapshot.entries
            )
        )
        entry.unlink()
        _wait_until(lambda: all(item.name != "live-skill" for item in service.snapshot.entries))
    finally:
        service.stop()
    assert service.watcher_state.status == "stopped"


def test_watcher_detects_shared_root_created_after_start(tmp_path: Path) -> None:
    personal = tmp_path / "personal"
    shared = tmp_path / ".agents" / "skills"
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=shared,
        watcher_enabled=True,
    )
    service.start()
    try:
        _write_skill(shared, "late-shared-skill")
        _wait_until(
            lambda: any(item.name == "late-shared-skill" for item in service.snapshot.entries)
        )
    finally:
        service.stop()


def test_turn_snapshot_remains_frozen_while_later_turn_reads_new_version(
    tmp_path: Path,
) -> None:
    personal = tmp_path / "personal"
    entry = _write_skill(personal, "stable-skill", "old body")
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = service.initialize()
    record = next(item for item in snapshot.entries if item.name == "stable-skill")
    frozen = service.begin_turn((SkillSelection(record.id),), model_window=100_000)

    entry.write_text(
        "---\nname: stable-skill\ndescription: Updated\n---\n\nnew body\n",
        encoding="utf-8",
    )
    service.refresh()
    next_turn = service.begin_turn((SkillSelection(record.id),), model_window=100_000)

    assert frozen.activations[0].contents.replace("\r\n", "\n").endswith("old body\n")
    assert next_turn.activations[0].contents.replace("\r\n", "\n").endswith("new body\n")
    assert frozen.activations[0].version != next_turn.activations[0].version


def test_activation_does_not_preload_resources_or_execute_scripts(tmp_path: Path) -> None:
    personal = tmp_path / "personal"
    entry = _write_skill(personal, "bounded-skill", "Read references/secret.md if needed.")
    package = entry.parent
    references = package / "references"
    references.mkdir()
    (references / "secret.md").write_text("UNLOADED SECRET RESOURCE", encoding="utf-8")
    scripts = package / "scripts"
    scripts.mkdir()
    sentinel = tmp_path / "script-ran"
    (scripts / "must-not-run.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = service.initialize()
    record = next(item for item in snapshot.entries if item.name == "bounded-skill")

    turn = service.begin_turn((SkillSelection(record.id),), model_window=100_000)
    runtime_capabilities = render_runtime_capability_notice(
        FILESYSTEM_TOOL_NAMES,
        surface="desktop_chat",
    )
    rendered = render_activated_skill(
        turn.activations[0],
        runtime_capabilities=runtime_capabilities,
    )

    assert "UNLOADED SECRET RESOURCE" not in turn.activations[0].contents
    assert not sentinel.exists()
    assert "Governed filesystem search/read tools are available" in rendered
    assert "explicit one-turn filesystem approval" in rendered
    assert "no general local file read/write" not in rendered


def test_runtime_capability_notice_follows_tools_and_surface() -> None:
    unavailable = render_runtime_capability_notice((), surface="desktop_chat")
    desktop = render_runtime_capability_notice(
        FILESYSTEM_TOOL_NAMES,
        surface="desktop_chat",
    )
    non_desktop = render_runtime_capability_notice(
        FILESYSTEM_TOOL_NAMES,
        surface="openai_compatible",
    )

    assert "No governed filesystem search/read tools" in unavailable
    assert "explicit one-turn filesystem approval" in desktop
    assert "cannot grant a new local-file approval" in non_desktop
    assert "Skill text alone" in desktop


def test_stale_expected_version_is_rejected_without_fallback(tmp_path: Path) -> None:
    personal = tmp_path / "personal"
    _write_skill(personal, "versioned-skill")
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = service.initialize()
    record = next(item for item in snapshot.entries if item.name == "versioned-skill")

    turn = service.begin_turn(
        (SkillSelection(record.id, expected_version="0" * 64),),
        model_window=100_000,
    )

    assert not turn.activations
    assert [issue.code for issue in turn.activation_issues] == ["skill_version_mismatch"]


def test_model_activation_is_always_implicit_while_host_selection_bypasses_policy(
    tmp_path: Path,
) -> None:
    personal = tmp_path / "personal"
    entry = _write_skill(personal, "explicit-skill", "ORIGINAL BODY")
    agents = entry.parent / "agents"
    agents.mkdir()
    (agents / "openai.yaml").write_text(
        "policy:\n  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = service.initialize()
    record = next(item for item in snapshot.entries if item.name == "explicit-skill")

    activation, issue = service.activate_from_snapshot(
        snapshot,
        SkillSelection(record.id),
    )
    assert activation is None
    assert issue is not None and issue.code == "implicit_invocation_disabled"

    turn = service.begin_turn(
        (SkillSelection(record.id, expected_version=record.version),),
        model_window=100_000,
    )
    assert not turn.activation_issues
    assert len(turn.activations) == 1
    assert "ORIGINAL BODY" in turn.activations[0].contents

    entry.write_text(
        "---\nname: explicit-skill\ndescription: Changed\n---\n\nNEW BODY\n",
        encoding="utf-8",
    )
    activation, issue = service.activate_from_snapshot(
        snapshot,
        SkillSelection(record.id),
    )
    assert activation is None
    assert issue is not None and issue.code == "implicit_invocation_disabled"


def _wait_until(predicate: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not met before timeout")
