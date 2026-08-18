from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agent.skills import SkillCatalogSnapshot, SkillRecord, resolve_explicit_skill_selections


def _record(name: str, index: int, *, implicit: bool = True) -> SkillRecord:
    entry = Path(f"/skills/{name}/SKILL.md")
    return SkillRecord(
        id=f"personal/{name}-{index}",
        source="personal",
        name=name,
        description=f"Use {name}",
        entry_path=entry,
        canonical_entry_path=entry,
        package_root=entry.parent,
        version=str(index) * 64,
        frontmatter={"name": name, "description": f"Use {name}"},
        allow_implicit_invocation=implicit,
    )


def _snapshot(*records: SkillRecord) -> SkillCatalogSnapshot:
    return SkillCatalogSnapshot(generation=1, fingerprint="fixture", entries=records)


def test_resolves_dollar_name_and_activation_verb_even_when_implicit_is_disabled() -> None:
    record = replace(_record("private-release-check", 1), allow_implicit_invocation=False)
    snapshot = _snapshot(record)

    dollar = resolve_explicit_skill_selections("$private-release-check", snapshot)
    natural = resolve_explicit_skill_selections("请直接用 private-release-check 检查", snapshot)

    assert dollar == natural
    assert len(dollar) == 1
    selection = dollar[0]
    assert selection.id == record.id
    assert selection.expected_version == record.version


def test_does_not_treat_incidental_or_negated_name_as_explicit_selection() -> None:
    snapshot = _snapshot(_record("context7-mcp", 2))

    assert not resolve_explicit_skill_selections("context7-mcp 是做什么的？", snapshot)
    assert not resolve_explicit_skill_selections("不要用 context7-mcp", snapshot)
    assert not resolve_explicit_skill_selections("不要用 $context7-mcp", snapshot)


def test_resolves_current_list_ordinal_from_latest_assistant_text() -> None:
    first = _record("skill-creator", 1)
    second = _record("context7-mcp", 2)
    snapshot = _snapshot(first, second)
    prior = "现在有两个 Skill：\n1. skill-creator：创建 Skill。\n2. context7-mcp：查文档。"

    selections = resolve_explicit_skill_selections(
        "你用一下2试试",
        snapshot,
        prior_assistant_text=prior,
    )

    assert [item.id for item in selections] == [second.id]
    assert not resolve_explicit_skill_selections(
        "不要 用 2",
        snapshot,
        prior_assistant_text=prior,
    )
