from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

from agent.sessions.search import SearchTextSegment, SessionMessageSearchResult
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.meta import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import SessionPersistError, SessionSearchResponse, SessionSearchService


def _client(service: SessionSearchService | None) -> TestClient:
    runtime = BridgeRuntime(
        settings=MagicMock(),
        persistent_store=MagicMock(),
        transient_store=MagicMock(),
        persistent_session_manager=MagicMock(),
        catalog=MagicMock(),
        tool_registry=MagicMock(),
        prompt_builder_factory=MagicMock(),
        default_persona="default",
        default_model="test/model",
        session_search_service=service,
    )
    app = FastAPI()
    register_routes(app, runtime)
    return TestClient(app)


def test_search_route_returns_response_and_forwards_limits() -> None:
    service = MagicMock(spec=SessionSearchService)
    service.search.return_value = SessionSearchResponse(
        query="needle",
        title_total=0,
        message_total=1,
        title_results=(),
        message_results=(
            SessionMessageSearchResult(
                result_id="message:s1:a1",
                kind="message",
                session_id="s1",
                session_title="Archived discussion",
                archived=True,
                role="assistant",
                occurred_at=datetime(2026, 8, 5, tzinfo=UTC),
                anchor_event_uuid="a1",
                matched_field="content",
                segments=(SearchTextSegment(text="needle", matched=True),),
            ),
        ),
    )

    response = _client(cast(SessionSearchService, service)).get(
        "/v1/sessions/search",
        params={"q": "needle", "message_limit": 12, "title_limit": 4},
    )

    assert response.status_code == 200
    assert response.json()["message_results"][0] == {
        "result_id": "message:s1:a1",
        "kind": "message",
        "session_id": "s1",
        "session_title": "Archived discussion",
        "archived": True,
        "role": "assistant",
        "occurred_at": "2026-08-05T00:00:00Z",
        "anchor_event_uuid": "a1",
        "matched_field": "content",
        "segments": [{"text": "needle", "matched": True}],
    }
    service.search.assert_called_once_with("needle", message_limit=12, title_limit=4)


def test_search_route_validates_query_and_limits() -> None:
    service = MagicMock(spec=SessionSearchService)
    service.search.side_effect = ValueError("q 不能为空")
    client = _client(cast(SessionSearchService, service))

    assert client.get("/v1/sessions/search", params={"q": "   "}).status_code == 400
    assert client.get("/v1/sessions/search", params={"q": "x" * 201}).status_code == 422
    assert (
        client.get(
            "/v1/sessions/search",
            params={"q": "x", "message_limit": 51},
        ).status_code
        == 422
    )


def test_search_route_maps_storage_errors_and_missing_service() -> None:
    service = MagicMock(spec=SessionSearchService)
    service.search.side_effect = SessionPersistError("search storage failed")

    failed = _client(cast(SessionSearchService, service)).get(
        "/v1/sessions/search", params={"q": "x"}
    )
    unavailable = _client(None).get("/v1/sessions/search", params={"q": "x"})

    assert failed.status_code == 500
    assert failed.json() == {"detail": "search storage failed"}
    assert unavailable.status_code == 503


def test_search_route_is_registered_before_dynamic_session_id() -> None:
    service = MagicMock(spec=SessionSearchService)
    service.search.return_value = SessionSearchResponse(
        query="x",
        title_total=0,
        message_total=0,
        title_results=(),
        message_results=(),
    )

    response = _client(cast(SessionSearchService, service)).get(
        "/v1/sessions/search", params={"q": "x"}
    )

    assert response.status_code == 200
    service.search.assert_called_once()
