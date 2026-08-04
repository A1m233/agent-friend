from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent.sessions.store import SessionStore

from agent import (
    Event,
    EventType,
    JsonlSessionStore,
    NotifyingSessionStore,
    Session,
    SessionCatalog,
    SessionChangeBroker,
    SessionMetadataStore,
    SessionRevisionStore,
    SessionSearchService,
    SessionSummary,
)


class _CountingStore:
    def __init__(self, inner: SessionStore) -> None:
        self.inner = inner
        self.load_count = 0

    def create(self, session: Session) -> None:
        self.inner.create(session)

    def append_event(self, session_id: str, event: Event) -> None:
        self.inner.append_event(session_id, event)

    def load(self, session_id: str) -> Session:
        self.load_count += 1
        return self.inner.load(session_id)

    def list(self) -> list[SessionSummary]:
        return self.inner.list()

    def delete(self, session_id: str) -> None:
        self.inner.delete(session_id)

    def latest(self) -> SessionSummary | None:
        return self.inner.latest()


def _event(
    event_type: EventType,
    event_uuid: str,
    minute: int,
    payload: dict[str, object],
    *,
    run_id: str | None = None,
) -> Event:
    meta = {"run_id": run_id} if run_id is not None else {}
    return Event(
        type=event_type,
        uuid=event_uuid,
        ts=datetime(2026, 8, 5, 1, minute, tzinfo=UTC),
        payload=payload,
        meta=meta,
    )


def _make_service(
    tmp_path: Path,
) -> tuple[SessionSearchService, _CountingStore, SessionMetadataStore]:
    revision_store = SessionRevisionStore(tmp_path / "session-revisions.json")
    broker = SessionChangeBroker(revision_store)
    notifying = NotifyingSessionStore(JsonlSessionStore(tmp_path / "sessions"), broker)
    store = _CountingStore(notifying)
    metadata = SessionMetadataStore(tmp_path / "metadata.json")
    catalog = SessionCatalog(store, metadata)
    service = SessionSearchService(store, catalog, broker)
    return service, store, metadata


def _create_session(store: _CountingStore, session_id: str, title: str) -> Session:
    session = Session.new(title, "default", "test/model", session_id=session_id)
    store.create(session)
    return session


def test_search_projects_only_user_visible_history_and_attachment_names(tmp_path: Path) -> None:
    service, store, _metadata = _make_service(tmp_path)
    session = _create_session(store, "s-visible", "Visible title")
    events = [
        _event(
            "user_message",
            "u-visible",
            1,
            {
                "content": "Needle in user text",
                "attachments": [
                    {
                        "asset_id": "asset-1",
                        "display_name": "Needle report.pdf",
                        "media_type": "application/pdf",
                        "kind": "document",
                    },
                    {"display_name": "invalid Needle attachment"},
                ],
            },
        ),
        _event("assistant_message", "a-first", 2, {"content": "First visible Needle"}),
        _event(
            "tool_call_request",
            "tool-request",
            3,
            {"tool_name": "search", "args": {"secret": "tool Needle"}},
        ),
        _event(
            "tool_call_result",
            "tool-result",
            4,
            {"content": "secret tool result Needle"},
        ),
        _event("assistant_message", "a-second", 5, {"content": "Second visible Needle"}),
        _event(
            "system_trigger",
            "trigger-pet",
            6,
            {"output_visibility": "user", "presentation": "pet"},
        ),
        _event("assistant_message", "a-pet-hidden", 7, {"content": "pet-only Needle"}),
        _event(
            "system_trigger",
            "trigger-hidden",
            8,
            {"output_visibility": "memory_only", "presentation": "none"},
            run_id="hidden-run",
        ),
        _event(
            "assistant_message",
            "a-run-hidden",
            9,
            {"content": "hidden-run Needle"},
            run_id="hidden-run",
        ),
        _event("user_message", "u-latest", 10, {"content": "next turn"}),
        _event(
            "assistant_message",
            "a-partial",
            11,
            {"content": "Persisted partial Needle", "partial": True},
        ),
        _event("model_context", "model-context", 12, {"content": "internal Needle"}),
        _event("user_message", "u-inactive", 13, {"content": "old branch Needle"}),
        _event(
            "turn_rewrite",
            "rewrite",
            14,
            {"inactive_event_uuids": ["u-inactive"]},
        ),
    ]
    for event in events:
        store.append_event(session.session_id, event)

    response = service.search("needle")

    assert response.message_total == 3
    assert [item.anchor_event_uuid for item in response.message_results] == [
        "a-partial",
        "a-first",
        "u-visible",
    ]
    assert response.message_results[1].role == "assistant"
    assert response.message_results[1].result_id == "message:s-visible:a-first"
    assert sum(segment.matched for segment in response.message_results[1].segments) == 1

    attachment_response = service.search("report")
    assert attachment_response.message_total == 1
    assert attachment_response.message_results[0].matched_field == "attachment_name"
    assert attachment_response.message_results[0].anchor_event_uuid == "u-visible"

    assert service.search("secret tool result").message_total == 0
    assert service.search("pet-only").message_total == 0
    assert service.search("old branch").message_total == 0


def test_search_matches_archived_titles_chinese_and_case_insensitively(tmp_path: Path) -> None:
    service, store, metadata = _make_service(tmp_path)
    session = _create_session(store, "s-archived", "Original title")
    store.append_event(
        session.session_id,
        _event("user_message", "u-cn", 1, {"content": "这里包含中文关键词"}),
    )
    metadata.set_display_title(session.session_id, "Archived NEEDLE discussion")
    metadata.set_archived_at(session.session_id, datetime(2026, 8, 5, tzinfo=UTC))

    title_response = service.search("needle")
    assert title_response.title_total == 1
    assert title_response.title_results[0].archived is True
    assert [
        segment.text for segment in title_response.title_results[0].segments if segment.matched
    ] == ["NEEDLE"]

    message_response = service.search("中文关")
    assert message_response.message_total == 1
    assert message_response.message_results[0].archived is True


def test_search_cache_reuses_unchanged_documents_and_invalidates_one_revision(
    tmp_path: Path,
) -> None:
    service, store, metadata = _make_service(tmp_path)
    first = _create_session(store, "s-first", "First")
    second = _create_session(store, "s-second", "Second")
    store.append_event(first.session_id, _event("user_message", "u-first", 1, {"content": "alpha"}))
    store.append_event(
        second.session_id,
        _event("user_message", "u-second", 2, {"content": "unrelated"}),
    )

    assert service.search("alpha").message_total == 1
    assert store.load_count == 2
    assert service.search("alpha").message_total == 1
    assert store.load_count == 2

    store.append_event(
        first.session_id,
        _event("assistant_message", "a-first", 3, {"content": "beta"}),
    )
    assert service.search("beta").message_total == 1
    assert store.load_count == 3

    metadata.set_display_title(first.session_id, "Renamed alpha")
    assert service.search("renamed").title_results[0].session_title == "Renamed alpha"
    assert store.load_count == 3

    store.delete(first.session_id)
    assert service.search("alpha").message_total == 0


def test_search_returns_stable_limited_results_and_validates_inputs(tmp_path: Path) -> None:
    service, store, _metadata = _make_service(tmp_path)
    base = datetime(2026, 8, 5, 1, tzinfo=UTC)
    for index in range(3):
        session = _create_session(store, f"s-{index}", f"Needle title {index}")
        store.append_event(
            session.session_id,
            Event(
                type="user_message",
                uuid=f"u-{index}",
                ts=base + timedelta(minutes=index),
                payload={"content": f"Needle content {index}"},
            ),
        )

    response = service.search("needle", title_limit=2, message_limit=2)
    assert response.title_total == 3
    assert response.message_total == 3
    assert [item.session_id for item in response.title_results] == ["s-2", "s-1"]
    assert [item.session_id for item in response.message_results] == ["s-2", "s-1"]

    for query, message_limit, title_limit in [
        (" ", 50, 10),
        ("x" * 201, 50, 10),
        ("x", 0, 10),
        ("x", 50, 11),
    ]:
        try:
            service.search(query, message_limit=message_limit, title_limit=title_limit)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid search input should fail")
