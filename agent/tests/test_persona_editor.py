from __future__ import annotations

import json
from pathlib import Path

from agent import (
    EffectivePersonaStore,
    PersonaEditorPromptBuilder,
    PersonaEditorWorkspace,
    ProposePersonaTool,
)


def _store(tmp_path: Path) -> EffectivePersonaStore:
    return EffectivePersonaStore(
        user_file=tmp_path / "personas" / "default" / "persona.md",
        internal_dir=tmp_path / "personas" / ".internal" / "default",
        builtin_loader=lambda: "初始人格",
    )


def test_editor_prompt_snapshot_freezes_current_persona_for_one_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.materialize()
    builder = PersonaEditorPromptBuilder(store)
    frozen = builder.snapshot()

    store.apply("外部更新后的人格", expected_revision=first.revision)

    assert "初始人格" in frozen.build()
    assert "外部更新后的人格" not in frozen.build()
    assert "外部更新后的人格" in builder.build()


def test_editor_prompt_uses_stable_ui_labels_instead_of_spatial_directions(
    tmp_path: Path,
) -> None:
    prompt = PersonaEditorPromptBuilder(_store(tmp_path)).build()

    assert "人格约束区" in prompt
    assert "候选版本" in prompt
    assert "应用修改" in prompt
    assert "可在右侧查看和应用" not in prompt
    assert "当前正式人格会在下方提供" not in prompt


def test_propose_persona_tool_returns_validated_candidate_with_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.materialize()
    result = ProposePersonaTool(store).invoke(
        {"content": "# Echo\n\n更直接。", "summary": ["表达更直接"]}
    )

    payload = json.loads(result.text)
    assert result.is_error is False
    assert payload == {
        "kind": "persona_candidate",
        "content": "# Echo\n\n更直接。",
        "displayName": "Echo",
        "summary": ["表达更直接"],
        "baseRevision": snapshot.revision,
    }
    assert ProposePersonaTool.execution_kind == "serial"
    assert ProposePersonaTool.result_context_retention == "pinned"


def test_propose_persona_tool_rejects_incomplete_payload(tmp_path: Path) -> None:
    result = ProposePersonaTool(_store(tmp_path)).invoke({"content": "只有正文"})

    assert result.is_error is True


def test_editor_workspace_keeps_original_revision_after_external_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.materialize()
    workspace = PersonaEditorWorkspace(store, tmp_path / "editor-base-revision.txt")
    assert workspace.start() == original.revision

    store.user_file.write_text("外部更新\n", encoding="utf-8")
    tool = ProposePersonaTool(store, base_revision_provider=workspace.base_revision)
    result = tool.invoke({"content": "旧编辑上下文生成的候选", "summary": ["继续调整"]})

    assert json.loads(result.text)["baseRevision"] == original.revision
    assert json.loads(result.text)["baseRevision"] != store.load().revision
