"""Strict decoding for the shared structured Skill selection wire shape."""

from __future__ import annotations

import re
from typing import Any

from .models import SkillSelection

_VERSION_RE = re.compile(r"^[0-9a-f]{64}$")


def decode_skill_selections(raw: Any) -> tuple[SkillSelection, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("skills 必须是数组")
    selections: list[SkillSelection] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"skills[{index}] 必须是对象")
        unknown = set(item) - {"id", "expected_version"}
        if unknown:
            raise ValueError(f"skills[{index}] 包含未知字段：{', '.join(sorted(unknown))}")
        skill_id = item.get("id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError(f"skills[{index}].id 必须是非空字符串")
        skill_id = skill_id.strip()
        expected = item.get("expected_version")
        if expected is not None and (
            not isinstance(expected, str) or _VERSION_RE.fullmatch(expected) is None
        ):
            raise ValueError(f"skills[{index}].expected_version 必须是完整 SHA-256")
        if skill_id in seen:
            continue
        seen.add(skill_id)
        selections.append(SkillSelection(id=skill_id, expected_version=expected))
    return tuple(selections)
