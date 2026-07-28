"""共享地点 resolver 组合行为。"""

from __future__ import annotations

from typing import ClassVar

from agent.tools.location import CompositeLocationResolver, LocationCandidate, LocationResolver


def _location(ref: str) -> LocationCandidate:
    return LocationCandidate(ref, ref, ref, None, None, 1.0, 2.0)


class _Resolver:
    name: ClassVar[str] = "stub"

    def __init__(self, results: tuple[LocationCandidate, ...] = ()) -> None:
        self.results = results
        self.search_calls = 0

    def search_locations(
        self, *, query: str, administrative_area: str | None
    ) -> tuple[LocationCandidate, ...]:
        self.search_calls += 1
        return self.results

    def get_location(self, *, ref: str) -> LocationCandidate | None:
        return next((item for item in self.results if item.ref == ref), None)


def test_primary_candidates_prevent_cross_provider_mix() -> None:
    primary = _Resolver((_location("primary"),))
    fallback = _Resolver((_location("fallback"),))
    resolver = CompositeLocationResolver(primary, fallback)

    assert resolver.search_locations(query="x", administrative_area=None) == primary.results
    assert fallback.search_calls == 0
    assert isinstance(resolver, LocationResolver)


def test_fallback_runs_only_when_primary_is_empty() -> None:
    primary = _Resolver()
    fallback = _Resolver((_location("fallback"),))
    resolver = CompositeLocationResolver(primary, fallback)

    assert resolver.search_locations(query="x", administrative_area="y") == fallback.results
    assert primary.search_calls == fallback.search_calls == 1
    assert resolver.get_location(ref="fallback") == fallback.results[0]
