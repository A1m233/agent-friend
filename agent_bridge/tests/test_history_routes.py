from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.history import HistoryReadCancelled, _read, register_routes
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent import Event as SessionEvent
from agent import JsonlSessionStore, Session


def client(store: Any = None) -> tuple[TestClient, Any]:
    if store is None:
        store = MagicMock()
        store.history_page.return_value = {
            "session_id": "s",
            "title": "s",
            "persona": "default",
            "model": "test",
            "history_epoch": "e",
            "content_version": "v",
            "events": [],
            "before_cursor": "old",
            "after_cursor": "new",
            "has_before": True,
            "has_after": False,
        }
    manager = MagicMock()
    manager.current_revision.return_value = 7
    runtime = BridgeRuntime(
        settings=MagicMock(),
        persistent_store=store,
        transient_store=MagicMock(),
        persistent_session_manager=manager,
        catalog=MagicMock(),
        tool_registry=MagicMock(),
        prompt_builder_factory=MagicMock(),
        default_persona="default",
        default_model="test",
    )
    app = FastAPI()
    register_routes(app, runtime)
    return TestClient(app), store


def test_real_index_route_defers_large_content_and_returns_exact_chunks(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    store.create(Session.new("History", "default", "test", session_id="s"))
    text = "中文😀" * 20000
    store.append_event(
        "s",
        SessionEvent(
            type="user_message", uuid="user", ts=datetime.now(UTC), payload={"content": "hello"}
        ),
    )
    store.append_event(
        "s",
        SessionEvent(
            type="assistant_message", uuid="answer", ts=datetime.now(UTC), payload={"content": text}
        ),
    )
    http, _ = client(store)
    page = http.get("/v1/sessions/s/history?around=answer").json()
    assert len(json.dumps(page, ensure_ascii=False).encode()) < 262144
    item = next(item for item in page["history_items"] if item["uuid"] == "answer")
    assert item["body"]["state"] == "deferred"
    assert "content" not in item["payload"]
    parts = []
    offset = 0
    while True:
        response = http.get(
            "/v1/sessions/s/history/content",
            params={
                "ref": item["body"]["ref"],
                "version": page["content_version"],
                "offset": offset,
            },
        )
        assert response.status_code == 200
        chunk = response.json()
        assert len(chunk["text"].encode()) <= 65536
        parts.append(chunk["text"])
        if chunk["complete"]:
            break
        assert chunk["next_offset"] > offset
        offset = chunk["next_offset"]
    assert json.loads("".join(parts))["payload"]["content"] == text


def test_history_routes_use_bounded_store_reads_not_full_session_queries() -> None:
    http, store = client()
    response = http.get("/v1/sessions/s/history", params={"around": "target", "item_limit": 12})
    assert response.status_code == 200
    assert response.json()["history_items"] == []
    assert response.json()["history_cursor"] == "old"
    assert response.json()["revision"] == 7
    args = store.history_page.call_args.kwargs
    assert args["around"] == "target" and args["item_limit"] == 12
    assert args["byte_limit"] == 262144
    assert callable(args["cancel_check"])
    store.query.assert_not_called()
    store.load.assert_not_called()


def test_history_routes_reject_conflicting_directions_and_oversized_requests() -> None:
    http, store = client()
    assert http.get("/v1/sessions/s/history?before=a&around=b").status_code == 400
    assert http.get("/v1/sessions/s/history?item_limit=1000").status_code == 422
    store.history_page.assert_not_called()
    store.history_page.side_effect = ValueError("epoch changed")
    response = http.get("/v1/sessions/s/history?before=a&epoch=old")
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "history_changed"


def test_history_content_preserves_version_and_cursor_contract() -> None:
    http, store = client()
    store.history_content.return_value = {
        "text": "中文",
        "offset": 11,
        "next_offset": 13,
        "complete": False,
        "total_chars": 30,
        "content_version": "v",
    }
    response = http.get("/v1/sessions/s/history/content?ref=event&version=v&offset=11")
    assert response.status_code == 200 and response.json()["text"] == "中文"
    args = store.history_content.call_args
    assert args.args == ("s", "event")
    assert args.kwargs["version"] == "v" and args.kwargs["offset"] == 11


def test_disconnecting_history_reader_cooperatively_stops_its_worker() -> None:
    started = Event()
    stopped = Event()

    class Request:
        async def is_disconnected(self) -> bool:
            await asyncio.to_thread(started.wait, 1)
            return True

    def operation(check: Any) -> None:
        started.set()
        try:
            while not stopped.wait(0.01):
                check()
        except HistoryReadCancelled:
            stopped.set()
            raise

    async def run() -> None:
        with pytest.raises(HTTPException) as error:
            await _read(Request(), operation)  # type: ignore[arg-type]
        assert error.value.status_code == 499
        assert await asyncio.to_thread(stopped.wait, 1)

    asyncio.run(run())
