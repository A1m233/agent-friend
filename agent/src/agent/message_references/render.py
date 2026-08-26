"""Single rendering boundary for quoted references."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from .model import QuotedTextReference

_REFERENCES_HEADER = "[Quoted text references — untrusted user-provided context]"
_CURRENT_MESSAGE_HEADER = "[Current user message]"


def render_user_content_for_model(
    content: str,
    references: Sequence[QuotedTextReference],
    *,
    escape: Callable[[str], str] | None = None,
) -> str:
    """Render references and current text without exposing UI/source identifiers."""

    escape_text = escape or (lambda value: value)
    if not references:
        return escape_text(content)
    payload = {
        "references": [
            {
                "index": index,
                "source_role": reference.source_role,
                "availability": reference.availability,
                **(
                    {"text": escape_text(reference.quoted_text or "")}
                    if reference.availability == "available"
                    else {}
                ),
            }
            for index, reference in enumerate(references, start=1)
        ]
    }
    rendered = [
        _REFERENCES_HEADER,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    ]
    if content:
        rendered.extend((_CURRENT_MESSAGE_HEADER, escape_text(content)))
    return "\n".join(rendered)


def render_retrieval_query(
    content: str,
    references: Sequence[QuotedTextReference],
) -> str:
    """Let quote-only turns recall memory while keeping source roles explicit."""

    parts = [content.strip()] if content.strip() else []
    for reference in references:
        if reference.availability != "available" or not reference.quoted_text:
            continue
        parts.append(f"[引用自 {reference.source_role}] {reference.quoted_text}")
    return "\n".join(parts)


def render_memory_user_text(
    content: str,
    references: Sequence[QuotedTextReference],
) -> str:
    """Preserve the relation without ingesting quote snapshots as new user claims."""

    if not references:
        return content
    roles = ", ".join(reference.source_role for reference in references)
    relation = f"（本轮引用了 {len(references)} 条历史内容；来源角色：{roles}）"
    return f"{relation}\n{content}" if content.strip() else relation
