from __future__ import annotations

from pathlib import Path

from agent.skills import SkillCatalogSnapshot, SkillRecord, render_catalog


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
    assert "structured selection" in first.content


def test_catalog_budget_omits_entries_and_fairly_truncates_descriptions() -> None:
    snapshot = SkillCatalogSnapshot(
        generation=1,
        fingerprint="snapshot",
        entries=tuple(_record(index, "x" * 1_024) for index in range(12)),
    )

    rendered = render_catalog(snapshot, model_window=8_000)

    assert len(rendered.content) <= int(8_000 * 0.02) * 4
    assert rendered.omitted_count > 0 or rendered.descriptions_truncated
