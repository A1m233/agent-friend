from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agent.skills import SkillCatalogSnapshot, SkillRecord, render_catalog
from agent.skills.catalog import catalog_entry_char_budget


def _record(index: int, description: str) -> SkillRecord:
    path = Path(f"/skills/skill-{index}/SKILL.md")
    return SkillRecord(
        id=f"skill_{index}",
        source="personal",
        name=f"skill-{index}",
        description=description,
        entry_path=path,
        canonical_entry_path=path,
        package_root=path.parent,
        version=f"version-{index}",
        frontmatter={"name": f"skill-{index}", "description": description},
    )


def test_catalog_is_deterministic_and_escapes_metadata() -> None:
    snapshot = SkillCatalogSnapshot(
        generation=1,
        fingerprint="snapshot",
        entries=(_record(1, "<close> & data"),),
    )

    first = render_catalog(snapshot, model_window=100_000)
    second = render_catalog(snapshot, model_window=100_000)

    assert first == second
    assert "&lt;close&gt; &amp; data" in first.content
    assert 'value="current"' in first.content
    assert 'sources="builtin,personal,shared"' in first.content
    assert 'history_baseline="not_guaranteed"' in first.content
    assert 'change_comparison="requires_trusted_prior_state"' in first.content
    assert '<catalog_coverage entries="complete" omitted_entries="0"/>' in first.content
    assert "call skill_read" in first.content
    assert "allow_implicit_invocation=true" in first.content


def test_catalog_hides_explicit_only_skill_from_model_visible_implicit_entries() -> None:
    record = _record(1, "Explicit only")
    record = replace(record, allow_implicit_invocation=False)
    snapshot = SkillCatalogSnapshot(generation=1, fingerprint="snapshot", entries=(record,))

    rendered = render_catalog(
        snapshot,
        model_window=100_000,
        semantic_activation_available=False,
    )

    assert "skill-1" not in rendered.content
    assert 'explicit_only_entries="1"' in rendered.content
    assert "available through host selection" in rendered.content
    assert "reader is unavailable" in rendered.content


def test_catalog_entry_budget_never_squeezes_fixed_semantics() -> None:
    snapshot = SkillCatalogSnapshot(
        generation=1,
        fingerprint="snapshot",
        entries=tuple(_record(index, "x" * 1_024) for index in range(12)),
    )

    rendered = render_catalog(snapshot, model_window=8_000)

    entry_budget, profile = catalog_entry_char_budget(8_000)
    entry_lines = [line for line in rendered.content.splitlines() if line.startswith("- ")]

    assert sum(len(line) + 1 for line in entry_lines) <= entry_budget
    assert len(rendered.content) > entry_budget
    assert rendered.budget_profile == profile == "entry_tokens:160"
    assert 'value="current"' in rendered.content
    assert 'history_baseline="not_guaranteed"' in rendered.content
    assert "successful skill_read activates" in rendered.content
    assert rendered.omitted_count > 0 or rendered.descriptions_truncated
    assert (
        '<catalog_coverage entries="truncated"' in rendered.content
        if rendered.omitted_count
        else '<catalog_coverage entries="complete"' in rendered.content
    )


def test_catalog_entry_budget_uses_independent_unknown_window_fallback() -> None:
    assert catalog_entry_char_budget(None) == (8_000, "entry_chars:8000")
