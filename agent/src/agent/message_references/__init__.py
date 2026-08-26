"""Unified quoted-text reference domain."""

from .model import (
    QuotedTextLocator,
    QuotedTextReference,
    QuotedTextSource,
    parse_quoted_text_references,
    quoted_text_references_fingerprint,
    quoted_text_references_to_dicts,
)
from .render import (
    render_memory_user_text,
    render_retrieval_query,
    render_user_content_for_model,
)

__all__ = [
    "QuotedTextLocator",
    "QuotedTextReference",
    "QuotedTextSource",
    "parse_quoted_text_references",
    "quoted_text_references_fingerprint",
    "quoted_text_references_to_dicts",
    "render_memory_user_text",
    "render_retrieval_query",
    "render_user_content_for_model",
]
