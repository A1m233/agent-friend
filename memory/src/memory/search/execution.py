"""Request-local search work and optional cooperative cancellation.

Callbacks stay out of SearchRequest and its serializable diagnostic contract.
Legacy/custom sources retain their search API; built-in sources can prepare
reusable work for the successive scope-expansion limits of one request.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from .contracts import AnalyzedQuery, EvidenceCandidate, EvidenceSource, SearchRequest

CancelCheck = Callable[[], None]
PreparedSearch = Callable[[int], list[EvidenceCandidate]]


def check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None:
        cancel_check()


def prepare_source_search(
    source: EvidenceSource,
    query: AnalyzedQuery,
    *,
    request: SearchRequest,
    cancel_check: CancelCheck | None,
) -> PreparedSearch:
    prepare = getattr(source, "prepare_search", None)
    if callable(prepare):
        return cast(PreparedSearch, prepare(query, request=request, cancel_check=cancel_check))

    def search(limit: int) -> list[EvidenceCandidate]:
        check_cancel(cancel_check)
        result = source.search(query, request=request, limit=limit)
        check_cancel(cancel_check)
        return result

    return search
