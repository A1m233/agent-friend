"""SessionHistorySource 的边界与候选投影测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent.memory_search import SessionHistorySource, SessionStoreSourceOrderProvider
from agent.sessions.events import SCHEMA_VERSION, Event
from agent.sessions.session import Session
from agent.sessions.store import SessionSummary
from memory.search import MemorySearchService, QueryAnalyzer, SearchRequest

_NOW = datetime(2026, 7, 14, 12, tzinfo=UTC)


class _Store:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self) -> list[SessionSummary]:
        return [
            SessionSummary(
                session_id=self._session.session_id,
                title="test",
                created_at=self._session.created_at,
                updated_at=self._session.events[-1].ts,
                persona="test",
                model="test/model",
            )
        ]

    def exists(self, session_id: str) -> bool:
        return session_id == self._session.session_id

    def load(self, session_id: str) -> Session:
        assert session_id == self._session.session_id
        return self._session

    def create(self, session: Session) -> None: ...
    def append_event(self, session_id: str, event: Event) -> None: ...
    def delete(self, session_id: str) -> None: ...
    def latest(self) -> SessionSummary | None: ...


def _session() -> Session:
    events = [
        Event(
            type="session_meta",
            uuid="session-1",
            ts=_NOW - timedelta(days=1),
            payload={
                "schema_version": SCHEMA_VERSION,
                "initial_title": "test",
                "initial_persona": "test",
                "initial_model": "test/model",
            },
        ),
        Event(
            type="user_message",
            uuid="u1",
            ts=_NOW - timedelta(hours=2),
            payload={"content": "我最近在学日语"},
            meta={"persona_id": "p1"},
        ),
        Event(
            type="assistant_message",
            uuid="a1",
            ts=_NOW - timedelta(hours=1, minutes=59),
            payload={"content": "可以试试影子跟读"},
            meta={"persona_id": "p1"},
        ),
        Event(
            type="user_message",
            uuid="u2",
            ts=_NOW - timedelta(minutes=1),
            payload={"content": "当前轮不要被召回"},
            meta={"persona_id": "p1"},
        ),
    ]
    return Session.from_events(events)


def test_source_honors_current_turn_boundary_and_keeps_pair_excerpt() -> None:
    service = MemorySearchService(
        [SessionHistorySource(_Store(_session()))],
        analyzer=QueryAnalyzer(lambda: _NOW),
    )

    result = service.search(
        SearchRequest(
            text="影子跟读",
            owner_user_id="local",
            persona_id="p1",
            mode="automatic",
            session_id="session-1",
            current_turn_start_index=3,
            top_k=10,
        )
    )

    assert [item.evidence_id for item in result.evidence] == ["session:session-1#turn:u1"]
    assert [excerpt.text for excerpt in result.evidence[0].excerpts] == [
        "我最近在学日语",
        "可以试试影子跟读",
    ]


def test_user_hit_keeps_following_assistant_as_evidence() -> None:
    service = MemorySearchService(
        [SessionHistorySource(_Store(_session()))],
        analyzer=QueryAnalyzer(lambda: _NOW),
    )

    result = service.search(
        SearchRequest(
            text="学日语",
            owner_user_id="local",
            persona_id="p1",
            mode="automatic",
            session_id="session-1",
            current_turn_start_index=3,
            top_k=10,
        )
    )

    assert [excerpt.text for excerpt in result.evidence[0].excerpts] == [
        "我最近在学日语",
        "可以试试影子跟读",
    ]


def test_source_filters_tagged_other_persona_but_keeps_legacy_untagged_events() -> None:
    session = _session()
    events = list(session.events)
    events.append(
        Event(
            type="assistant_message",
            uuid="other",
            ts=_NOW,
            payload={"content": "另一个人格的回答"},
            meta={"persona_id": "p2"},
        )
    )
    service = MemorySearchService([SessionHistorySource(_Store(Session.from_events(events)))])

    result = service.search(
        SearchRequest(
            text="人格",
            owner_user_id="local",
            persona_id="p1",
            mode="explicit",
            top_k=10,
        )
    )

    assert result.evidence == ()


def test_summary_groups_adjacent_messages_into_non_overlapping_turns() -> None:
    session = _session()
    events = list(session.events[:3])
    events.extend(
        [
            Event(
                type="assistant_message",
                uuid="a1-followup",
                ts=_NOW - timedelta(hours=1, minutes=58),
                payload={"content": "再补充一个跟读建议"},
                meta={"persona_id": "p1"},
            ),
            Event(
                type="user_message",
                uuid="u2-summary",
                ts=_NOW - timedelta(hours=1),
                payload={"content": "第二轮用户问题"},
                meta={"persona_id": "p1"},
            ),
            Event(
                type="assistant_message",
                uuid="a2-summary",
                ts=_NOW - timedelta(minutes=59),
                payload={"content": "第二轮助手回答"},
                meta={"persona_id": "p1"},
            ),
        ]
    )
    service = MemorySearchService([SessionHistorySource(_Store(Session.from_events(events)))])

    result = service.search(
        SearchRequest(
            text="",
            owner_user_id="local",
            persona_id="p1",
            mode="explicit",
            top_k=10,
            retrieval_goal="summary",
        )
    )

    assert len(result.evidence) == 2
    assert [[excerpt.text for excerpt in item.excerpts] for item in result.evidence] == [
        ["第二轮用户问题", "第二轮助手回答"],
        ["我最近在学日语", "可以试试影子跟读", "再补充一个跟读建议"],
    ]


def test_search_returns_one_turn_when_multiple_messages_match() -> None:
    session = _session()
    events = list(session.events)
    events[1] = Event(
        type="user_message",
        uuid="u1",
        ts=_NOW - timedelta(hours=2),
        payload={"content": "共同词在用户问题里"},
        meta={"persona_id": "p1"},
    )
    events[2] = Event(
        type="assistant_message",
        uuid="a1",
        ts=_NOW - timedelta(hours=1, minutes=59),
        payload={"content": "共同词也在助手回答里"},
        meta={"persona_id": "p1"},
    )
    service = MemorySearchService([SessionHistorySource(_Store(Session.from_events(events)))])

    result = service.search(
        SearchRequest(
            text="共同词",
            owner_user_id="local",
            persona_id="p1",
            mode="explicit",
            top_k=10,
        )
    )

    assert len(result.evidence) == 1
    assert [excerpt.text for excerpt in result.evidence[0].excerpts] == [
        "共同词在用户问题里",
        "共同词也在助手回答里",
    ]


def test_user_match_does_not_pull_previous_turn_assistant() -> None:
    session = _session()
    events = list(session.events)
    events.append(
        Event(
            type="assistant_message",
            uuid="a2",
            ts=_NOW,
            payload={"content": "当前轮对应回答"},
            meta={"persona_id": "p1"},
        )
    )
    service = MemorySearchService([SessionHistorySource(_Store(Session.from_events(events)))])

    result = service.search(
        SearchRequest(
            text="当前轮不要被召回",
            owner_user_id="local",
            persona_id="p1",
            mode="explicit",
            top_k=10,
        )
    )

    assert [excerpt.text for excerpt in result.evidence[0].excerpts] == [
        "当前轮不要被召回",
        "当前轮对应回答",
    ]


def test_source_order_provider_projects_only_completed_active_conversation_messages() -> None:
    events = list(_session().events)
    events.extend(
        [
            Event(
                type="assistant_message",
                uuid="partial",
                ts=_NOW,
                payload={"content": "未完成", "partial": True},
            ),
            Event(
                type="compaction",
                uuid="compaction",
                ts=_NOW + timedelta(seconds=1),
                payload={"summary": "摘要", "covered_through_uuid": "a1"},
            ),
        ]
    )
    provider = SessionStoreSourceOrderProvider(_Store(Session.from_events(events)))

    assert provider.active_conversation_event_uuids("session-1") == ("u1", "a1", "u2")
