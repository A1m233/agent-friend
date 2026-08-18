"""Host-owned decoding and deterministic explicit Skill selection."""

from __future__ import annotations

import re
from typing import Any

from .models import SkillCatalogSnapshot, SkillRecord, SkillSelection

_VERSION_RE = re.compile(r"^[0-9a-f]{64}$")
_NAME_BOUNDARY = r"A-Za-z0-9_-"
_ACTION = r"(?:用|使用|调用|启用|激活|选择|选|采用|use|invoke|activate|select)"
_NEGATED_ACTION = rf"(?:不要|别|不用|无需|无须|不)\s*(?:再)?\s*{_ACTION}"
_ORDINAL_REQUEST_RE = re.compile(
    rf"{_ACTION}(?:一下|下)?\s*(?:第\s*)?([1-9][0-9]*)",
    re.IGNORECASE,
)
_ENUMERATED_SKILL_RE = re.compile(
    r"^\s*([1-9][0-9]*)\s*[.、)）]\s*[`*_]*([A-Za-z0-9][A-Za-z0-9_-]*)",
    re.MULTILINE,
)


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


def resolve_explicit_skill_selections(
    user_input: str,
    snapshot: SkillCatalogSnapshot,
    *,
    prior_assistant_text: str | None = None,
) -> tuple[SkillSelection, ...]:
    """Resolve only host-verifiable explicit requests.

    The model never supplies the invocation class. ``$name``, an exact skill name used
    with an activation verb, and an ordinal pointing at the latest enumerated assistant
    list are deterministic caller intent. Description matching and conversational
    references remain implicit and must pass the Skill policy at read time.
    """

    records_by_name: dict[str, list[SkillRecord]] = {}
    for record in snapshot.entries:
        records_by_name.setdefault(record.name.casefold(), []).append(record)

    selected: list[SkillSelection] = []
    seen: set[str] = set()

    def add_name(raw_name: str) -> None:
        matches = records_by_name.get(raw_name.casefold(), [])
        if len(matches) != 1:
            return
        record = matches[0]
        record_id = record.id
        if record_id in seen:
            return
        seen.add(record_id)
        selected.append(
            SkillSelection(
                id=record_id,
                expected_version=record.version,
            )
        )

    stripped = user_input.strip()
    folded = stripped.casefold()
    for name in sorted(records_by_name, key=len, reverse=True):
        escaped_name = re.escape(name)
        name_re = re.compile(
            rf"(?<![{_NAME_BOUNDARY}]){escaped_name}(?![{_NAME_BOUNDARY}])",
            re.IGNORECASE,
        )
        for match in name_re.finditer(user_input):
            prefix = user_input[max(0, match.start() - 32) : match.start()]
            explicit_token = prefix.rstrip().endswith("$")
            negated = re.search(rf"{_NEGATED_ACTION}\s*\$?\s*$", prefix, re.IGNORECASE) is not None
            action = re.search(rf"{_ACTION}(?:一下|下)?\s*$", prefix, re.IGNORECASE) is not None
            exact_only = folded in {name, f"{name} skill"}
            if not negated and (explicit_token or exact_only or action):
                add_name(name)
                break

    if prior_assistant_text:
        ordinal_to_name: dict[int, str] = {
            int(number): name for number, name in _ENUMERATED_SKILL_RE.findall(prior_assistant_text)
        }
        for match in _ORDINAL_REQUEST_RE.finditer(user_input):
            action_prefix = user_input[max(0, match.start() - 12) : match.start()]
            if re.search(
                r"(?:不要|别|不用|无需|无须|不)\s*(?:再)?\s*$",
                action_prefix,
                re.IGNORECASE,
            ):
                continue
            ordinal_name = ordinal_to_name.get(int(match.group(1)))
            if ordinal_name is not None:
                add_name(ordinal_name)

    return tuple(selected)
