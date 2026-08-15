"""Stable Core Profile rendering for model context."""

from __future__ import annotations

from .contracts import CORE_FIELD_SPECS, CoreProfile


class CoreProfileRenderer:
    def render(self, profile: CoreProfile) -> str:
        if profile.is_empty():
            return ""
        grouped: dict[str, list[str]] = {}
        for entry in profile.entries:
            grouped.setdefault(entry.field_key, []).append(entry.value)
        lines = ["# 你始终知道的当前核心资料"]
        for field_key, spec in CORE_FIELD_SPECS.items():
            values = grouped.get(field_key)
            if not values:
                continue
            rendered = "、".join(values) if spec.cardinality == "many" else values[0]
            lines.append(f"- {spec.label}：{rendered}")
        return "\n".join(lines)
