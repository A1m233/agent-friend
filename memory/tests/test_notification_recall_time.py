"""Notification time presentation across real SQLite recall and trace boundaries."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal
from unittest.mock import Mock

import pytest
from memory.contracts import RecallTrace
from memory.host_episodes import presentation
from memory.retrieval import renderer
from memory.search import SearchRequest

from memory import (
    EpisodicRow,
    Extractor,
    KeywordRetrieval,
    Memory,
    MemorySearchService,
    Reconciler,
    SqliteMemoryStore,
    StructuredMemorySource,
)

_OCCURRED = datetime(2026, 9, 6, 9, 9, 27, 18153, tzinfo=UTC)
_BODY = '，我通过桌宠气泡就「喝水」发出提醒，实际显示："17:10 提醒你。"。'
_OLD_SUMMARY = _OCCURRED.isoformat() + _BODY
_LOCAL_SUMMARY = "2026-09-06 17:09:27 +08:00" + _BODY


@pytest.fixture
def local_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    zone = timezone(timedelta(hours=8))
    monkeypatch.setattr(presentation, "_local_time", lambda value: value.astimezone(zone))
    monkeypatch.setattr(renderer, "_local_date", lambda value: value.astimezone(zone).date())


def _store() -> SqliteMemoryStore:
    store = SqliteMemoryStore(":memory:")
    store.add_episodic(
        EpisodicRow(
            id="notification-presented:delivery",
            source_ref="notification:delivery",
            persona_id="p1",
            summary=_OLD_SUMMARY,
            occurred_at=_OCCURRED,
            created_at=_OCCURRED,
        )
    )
    return store


@pytest.mark.parametrize("unified_search", [True, False])
@pytest.mark.parametrize("source", ["natural", "probe"])
def test_existing_notification_recall_and_trace_use_local_time(
    local_timezone: None, unified_search: bool, source: Literal["natural", "probe"]
) -> None:
    store = _store()
    traces: list[RecallTrace] = []
    extractor = Mock(spec=Extractor)
    memory = Memory(
        store,
        extractor,
        Reconciler(store),
        retrieval=KeywordRetrieval(store),
        search_service=(
            MemorySearchService([StructuredMemorySource(store)]) if unified_search else None
        ),
        on_retrieved=traces.append,
    )
    try:
        context = memory.retrieve("喝水", persona_id="p1", source=source)
        assert [item.text for item in context.items] == [_LOCAL_SUMMARY]
        assert _LOCAL_SUMMARY in context.rendered
        assert [item.text for item in traces[-1].items] == [_LOCAL_SUMMARY]
        assert store.list_episodic(owner_user_id="local")[0].summary == _OLD_SUMMARY
        assert store.list_episodic(owner_user_id="local")[0].occurred_at == _OCCURRED
        extractor.extract.assert_not_called()
    finally:
        memory.close()


def test_explicit_search_presents_local_time_after_ranking(local_timezone: None) -> None:
    store = _store()
    try:
        result = MemorySearchService([StructuredMemorySource(store)]).search(
            SearchRequest(text="喝水", owner_user_id="local", persona_id="p1", mode="explicit")
        )
        assert result.evidence[0].text == _LOCAL_SUMMARY
        assert result.candidates[0].text == _OLD_SUMMARY
        assert result.evidence[0].occurred_at == _OCCURRED
        assert result.evidence[0].provenance == ("notification:delivery",)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("occurred", "now", "expected"),
    [
        ("2026-09-06T16:30:00+00:00", "2026-09-06T17:00:00+00:00", "2026-09-07，今天"),
        ("2026-09-06T15:59:00+00:00", "2026-09-06T16:01:00+00:00", "2026-09-06，昨天"),
    ],
)
def test_recall_day_hint_follows_local_calendar(
    local_timezone: None, occurred: str, now: str, expected: str
) -> None:
    store = _store()
    try:
        result = MemorySearchService([StructuredMemorySource(store)]).search(
            SearchRequest(text="喝水", owner_user_id="local", persona_id="p1", mode="explicit")
        )
        candidate = replace(result.evidence[0], occurred_at=datetime.fromisoformat(occurred))
        context = renderer.Renderer().render_search([candidate], now=datetime.fromisoformat(now))
        assert f"（{expected}）" in context.rendered
    finally:
        store.close()
