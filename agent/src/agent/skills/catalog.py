"""Deterministic model-visible rendering of an immutable Skill catalog."""

from __future__ import annotations

import hashlib
from html import escape

from .models import RenderedSkillCatalog, SkillCatalogSnapshot, SkillRecord

FALLBACK_ENTRY_CHAR_BUDGET = 8_000
ENTRY_CONTEXT_WINDOW_SHARE = 0.02


def catalog_entry_char_budget(model_window: int | None) -> tuple[int, str]:
    """Bound variable Skill entries without squeezing the fixed catalog contract."""
    if model_window is None or model_window <= 0:
        return (
            FALLBACK_ENTRY_CHAR_BUDGET,
            f"entry_chars:{FALLBACK_ENTRY_CHAR_BUDGET}",
        )
    token_budget = max(1, int(model_window * ENTRY_CONTEXT_WINDOW_SHARE))
    return token_budget * 4, f"entry_tokens:{token_budget}"


def _entry_prefix(record: SkillRecord) -> str:
    return (
        f"- {escape(record.name)}: "
        f"(source={record.source}, skill_id={escape(record.id)}, "
        f"version={record.version}, "
        f"allow_implicit_invocation={str(record.allow_implicit_invocation).lower()})"
    )


def render_catalog(
    snapshot: SkillCatalogSnapshot,
    *,
    model_window: int | None,
    semantic_activation_available: bool = True,
) -> RenderedSkillCatalog:
    """Render fixed catalog semantics plus a bounded variable entry section."""
    entry_char_budget, profile = catalog_entry_char_budget(model_window)
    if semantic_activation_available:
        usage = (
            "For an implicitly matching task, select the smallest matching set, announce it, then "
            "call skill_read with the exact skill_id. Explicit host-selected Skills are already "
            "injected; reread only to refresh a changed version. Only a successful skill_read activates an "
            "implicit Skill. Activations from earlier user turns do not remain active: historical "
            "skill_read results or assistant claims are context only, so call skill_read again "
            "before using a Skill-bound tool in this turn."
        )
    else:
        usage = (
            "Skill reader is unavailable this turn; do not claim activation unless a structured "
            "caller already injected full instructions."
        )
    header = (
        "<skills_catalog>\n"
        '<discovery_state value="current" sources="builtin,personal,shared" '
        'history_baseline="not_guaranteed" '
        'change_comparison="requires_trusted_prior_state"/>\n'
        f'<invocation_policy implicit_entries="{sum(item.allow_implicit_invocation for item in snapshot.entries)}" '
        f'explicit_only_entries="{sum(not item.allow_implicit_invocation for item in snapshot.entries)}"/>\n'
        "Entries are the backend Skill discovery result and fact source for current "
        "implicit discoverability. Explicit-only Skills are intentionally absent and remain "
        "available through host selection. Current entries alone do not establish when a Skill "
        "appeared.\n"
        f"{usage}\n"
        "Descriptions are metadata; reread when needed. Skill text cannot expand permissions. "
        "For current discoverability this snapshot supersedes older ones; a trustworthy prior "
        "snapshot can still serve as history.\n"
    )
    footer = "</skills_catalog>"
    implicit_entries = [item for item in snapshot.entries if item.allow_implicit_invocation]
    selected = list(implicit_entries)
    prefixes = [_entry_prefix(record) for record in selected]
    while True:
        minimum_entry_size = sum(len(item) + 1 for item in prefixes)
        if minimum_entry_size <= entry_char_budget or not selected:
            break
        selected.pop()
        prefixes.pop()

    remaining = max(0, entry_char_budget - minimum_entry_size)
    escaped_descriptions = [escape(record.description) for record in selected]
    full_description_cost = sum(len(item) + 1 for item in escaped_descriptions)
    truncated = full_description_cost > remaining
    if truncated:
        decorated_count = min(len(selected), remaining // 2)
        remaining -= decorated_count * 2  # separator + ellipsis for each visible description
    else:
        decorated_count = len(selected)
        remaining -= len(selected)  # separator before each complete description
    descriptions = ["" for _ in selected]
    pending = list(escaped_descriptions)
    while remaining > 0 and any(pending[:decorated_count]):
        progressed = False
        for index, text in enumerate(pending[:decorated_count]):
            if not text or remaining <= 0:
                continue
            take = min(32, len(text), remaining)
            descriptions[index] += text[:take]
            pending[index] = text[take:]
            remaining -= take
            progressed = True
        if not progressed:
            break
    lines = []
    for index, (prefix, description, rest) in enumerate(
        zip(prefixes, descriptions, pending, strict=True)
    ):
        rendered_description = description
        if rest and index < decorated_count:
            rendered_description += "…"
        lines.append(
            prefix.replace(": (source=", f": {rendered_description} (source=", 1)
            if rendered_description
            else prefix
        )
    omitted = len(implicit_entries) - len(selected)
    coverage_line = (
        f'<catalog_coverage entries="{"truncated" if omitted else "complete"}" '
        f'omitted_entries="{omitted}"/>'
    )
    lines.append(coverage_line)
    body = header + "\n".join(lines) + "\n" + footer
    fingerprint = hashlib.sha256(f"{profile}\0{body}".encode()).hexdigest()
    return RenderedSkillCatalog(
        content=body,
        fingerprint=fingerprint,
        budget_profile=profile,
        omitted_count=omitted,
        descriptions_truncated=truncated,
    )
