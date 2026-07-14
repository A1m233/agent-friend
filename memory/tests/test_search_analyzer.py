"""M48.1 共享 query/time analyzer 回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.search import QueryAnalyzer, SearchRequest, TimeRange

_TZ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 7, 14, 15, 30, tzinfo=_TZ)


def _request(text: str, *, explicit: TimeRange | None = None) -> SearchRequest:
    return SearchRequest(
        text=text,
        owner_user_id="local",
        persona_id="p1",
        mode="explicit",
        explicit_time_range=explicit,
    )


def test_infers_supported_time_range_and_removes_recall_scaffolding() -> None:
    analyzed = QueryAnalyzer(lambda: _NOW).analyze(_request("上周我们聊过什么？"))

    assert analyzed.intent == "temporal"
    assert analyzed.time_range == TimeRange(
        since=datetime(2026, 7, 6, tzinfo=_TZ),
        until=datetime(2026, 7, 13, tzinfo=_TZ),
    )
    assert analyzed.retrieval_text == ""
    assert analyzed.tokens == ()


def test_explicit_time_range_wins_over_natural_language() -> None:
    explicit = TimeRange(
        since=datetime(2026, 1, 1, tzinfo=_TZ),
        until=datetime(2026, 2, 1, tzinfo=_TZ),
    )
    analyzed = QueryAnalyzer(lambda: _NOW).analyze(_request("上周聊过日语", explicit=explicit))

    assert analyzed.time_range is explicit
    assert analyzed.retrieval_text == "日语"
    assert analyzed.tokens == ("日语",)


def test_does_not_invent_range_for_vague_recent_phrase() -> None:
    analyzed = QueryAnalyzer(lambda: _NOW).analyze(_request("最近我们聊了什么"))

    assert analyzed.time_range is None


def test_tokenizes_chinese_query_for_shared_history_search() -> None:
    analyzed = QueryAnalyzer(lambda: _NOW).analyze(_request("Tom是什么？"))

    assert analyzed.tokens == ("tom",)


def test_detects_identity_and_state_intents() -> None:
    analyzer = QueryAnalyzer(lambda: _NOW)

    identity = analyzer.analyze(_request("你还记得我吗？"))
    historical = analyzer.analyze(_request("我以前在哪里工作？"))
    current = analyzer.analyze(_request("我现在在哪里工作？"))

    assert identity.intent == "identity"
    assert identity.identity_requested is True
    assert historical.intent == "historical_state"
    assert historical.include_historical is True
    assert current.intent == "current_state"
    assert current.include_historical is False


def test_time_range_rejects_naive_or_reversed_boundaries() -> None:
    try:
        TimeRange(since=datetime(2026, 1, 1))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("naive datetime should fail")

    try:
        TimeRange(since=_NOW, until=_NOW)
    except ValueError as exc:
        assert "早于" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("reversed range should fail")
