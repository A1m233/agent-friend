from __future__ import annotations

from pathlib import Path

import pytest
from agent.errors import PersonaRevisionConflictError, PersonaUndoUnavailableError
from agent.personas import (
    PERSONA_DISPLAY_NAME_FALLBACK,
    EffectivePersonaStore,
    extract_persona_display_name,
)


def make_store(tmp_path: Path) -> EffectivePersonaStore:
    return EffectivePersonaStore(
        user_file=tmp_path / "personas" / "default" / "persona.md",
        internal_dir=tmp_path / "personas" / ".internal" / "default",
        builtin_loader=lambda: "builtin persona",
    )


def test_first_load_creates_plain_persona_file_from_builtin(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    initial = store.load()
    assert initial.content == "builtin persona"
    assert initial.display_name == PERSONA_DISPLAY_NAME_FALLBACK
    assert initial.source == "user"
    assert initial.source_status == "valid"
    assert store.user_file.read_text(encoding="utf-8") == "builtin persona\n"
    assert not store.user_file.read_text(encoding="utf-8").startswith("---")


def test_builtin_persona_exposes_complete_editable_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_FRIEND_DATA_DIR", str(tmp_path / "data"))
    store = EffectivePersonaStore(
        user_file=tmp_path / "personas" / "default" / "persona.md",
        internal_dir=tmp_path / "personas" / ".internal" / "default",
    )

    content = store.load().content

    assert content.startswith("# Echo\n")
    assert "## 身份与关系" in content
    assert "## 性格底色与价值取向" in content
    assert "## 相处方式" in content
    assert "## 情绪回应与交流节奏" in content
    assert "## 说话风格" in content
    assert "## 典型回应" in content
    assert not content.startswith("---")


def test_display_name_comes_from_first_h1_without_becoming_a_second_truth_source() -> None:
    assert extract_persona_display_name("说明\n# **Luna**\n\n正文") == "Luna"
    assert extract_persona_display_name("```md\n# 假标题\n```\n# Momo #") == "Momo"
    assert extract_persona_display_name("## 不是名字\n\n没有一级标题") == "你的朋友"


def test_invalid_external_edit_uses_last_valid_and_changes_revision(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.materialize()
    applied = store.apply("gentle persona", expected_revision=first.revision)

    store.user_file.write_text("", encoding="utf-8")
    invalid = store.load()

    assert invalid.source == "last_valid"
    assert invalid.source_status == "invalid"
    assert invalid.content == "gentle persona"
    assert invalid.revision != applied.revision
    assert invalid.diagnostic is not None


def test_apply_rejects_stale_revision_after_external_change(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    base = store.materialize()
    store.user_file.write_text("external change\n", encoding="utf-8")

    with pytest.raises(PersonaRevisionConflictError):
        store.apply("stale draft", expected_revision=base.revision)

    assert store.load().content == "external change"


def test_guided_apply_has_single_use_undo_and_reset(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    base = store.materialize()
    applied = store.apply("new persona", expected_revision=base.revision)
    assert applied.content == "new persona"
    assert applied.can_undo_apply is True

    undone = store.undo_apply()
    assert undone.content == "builtin persona"
    assert undone.can_undo_apply is False
    with pytest.raises(PersonaUndoUnavailableError):
        store.undo_apply()

    reset = store.reset()
    assert reset.content == "builtin persona"
    assert reset.source == "user"
    assert store.user_file.read_text(encoding="utf-8") == "builtin persona\n"


def test_deleted_external_file_is_recreated_from_builtin(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    base = store.materialize()
    store.apply("custom", expected_revision=base.revision)
    store.user_file.unlink()

    snapshot = store.load()
    assert snapshot.source == "user"
    assert snapshot.source_status == "valid"
    assert snapshot.content == "builtin persona"
    assert store.user_file.read_text(encoding="utf-8") == "builtin persona\n"
