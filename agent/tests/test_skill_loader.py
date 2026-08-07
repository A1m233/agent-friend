from __future__ import annotations

import os
from pathlib import Path

import pytest
from agent.skills import SkillLoader, SkillRoot


def _write_skill(root: Path, name: str, *, description: str = "Useful workflow") -> Path:
    package = root / name
    package.mkdir(parents=True)
    entry = package / "SKILL.md"
    entry.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return entry


def test_loader_discovers_sources_and_keeps_same_name(tmp_path: Path) -> None:
    personal = tmp_path / "personal"
    shared = tmp_path / "shared"
    _write_skill(personal, "same-skill", description="Personal")
    _write_skill(shared, "same-skill", description="Shared")

    snapshot = SkillLoader().load(
        (SkillRoot("personal", personal), SkillRoot("shared", shared)),
        generation=4,
    )

    assert snapshot.generation == 4
    assert [(item.source, item.name) for item in snapshot.entries] == [
        ("personal", "same-skill"),
        ("shared", "same-skill"),
    ]
    assert snapshot.entries[0].id != snapshot.entries[1].id
    assert sum(issue.code == "duplicate_name" for issue in snapshot.issues) == 2


def test_loader_isolates_invalid_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "valid-skill")
    bad = root / "bad-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("# missing metadata\n", encoding="utf-8")

    snapshot = SkillLoader().load((SkillRoot("personal", root),))

    assert [item.name for item in snapshot.entries] == ["valid-skill"]
    assert any(issue.code == "missing_frontmatter" for issue in snapshot.issues)


def test_loader_reads_openai_implicit_policy_and_fails_safe(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    explicit_entry = _write_skill(root, "explicit-skill")
    explicit_agents = explicit_entry.parent / "agents"
    explicit_agents.mkdir()
    (explicit_agents / "openai.yaml").write_text(
        "policy:\n  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )
    invalid_entry = _write_skill(root, "invalid-policy")
    invalid_agents = invalid_entry.parent / "agents"
    invalid_agents.mkdir()
    (invalid_agents / "openai.yaml").write_text(
        "policy:\n  allow_implicit_invocation: nope\n",
        encoding="utf-8",
    )

    snapshot = SkillLoader().load((SkillRoot("personal", root),))
    by_name = {item.name: item for item in snapshot.entries}

    assert by_name["explicit-skill"].allow_implicit_invocation is False
    assert by_name["invalid-policy"].allow_implicit_invocation is False
    assert any(issue.code == "invalid_openai_policy" for issue in snapshot.issues)


def test_loader_rejects_name_directory_mismatch_and_large_file(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    mismatch = root / "directory-name"
    mismatch.mkdir(parents=True)
    (mismatch / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: mismatch\n---\n",
        encoding="utf-8",
    )
    huge = root / "huge-skill"
    huge.mkdir()
    (huge / "SKILL.md").write_text(
        "---\nname: huge-skill\ndescription: huge\n---\n" + ("x" * 512),
        encoding="utf-8",
    )

    snapshot = SkillLoader(max_skill_bytes=128).load((SkillRoot("personal", root),))

    assert not snapshot.entries
    assert {issue.code for issue in snapshot.issues} >= {
        "name_directory_mismatch",
        "skill_too_large",
    }


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs optional privilege")
def test_loader_dedupes_symlink_alias_and_tracks_external_target(tmp_path: Path) -> None:
    personal = tmp_path / "personal"
    shared = tmp_path / "shared"
    target = tmp_path / "external" / "linked-skill"
    _write_skill(target.parent, target.name)
    personal.mkdir()
    shared.mkdir()
    (personal / "linked-skill").symlink_to(target, target_is_directory=True)
    (shared / "linked-skill").symlink_to(target, target_is_directory=True)

    snapshot = SkillLoader().load((SkillRoot("personal", personal), SkillRoot("shared", shared)))

    assert len(snapshot.entries) == 1
    assert len(snapshot.entries[0].aliases) == 1
    assert target.resolve() in snapshot.external_watch_roots
    assert any(issue.code == "canonical_duplicate" for issue in snapshot.issues)
