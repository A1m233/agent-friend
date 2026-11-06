from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent.message_exposure import content_version
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.message_exposure import MessageExposureService
from agent_bridge.presence import PresenceRegistry
from agent_bridge.routes.presence import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import Event, JsonlSessionStore, Session, SessionManager


@pytest.fixture
def setup(tmp_path: Path) -> tuple[TestClient, MessageExposureService, JsonlSessionStore]:
    store = JsonlSessionStore(tmp_path / "sessions")
    store.create(Session.new("display", "default", "test/model", session_id="s"))
    store.append_event(
        "s",
        Event(
            type="assistant_message", uuid="a", ts=datetime.now(UTC), payload={"content": "正文"}
        ),
    )
    runtime = cast(
        BridgeRuntime,
        SimpleNamespace(
            settings=SimpleNamespace(sessions_dir=tmp_path / "sessions"),
            persistent_session_manager=SessionManager(store),
            session_delivery=None,
            presence_registry=PresenceRegistry(),
        ),
    )
    service = MessageExposureService(runtime)
    cast(Any, runtime).message_exposure = service
    app = FastAPI()
    register_routes(app, runtime)
    return TestClient(app), service, store


@pytest.mark.parametrize(
    "override",
    [
        {"windowVisible": False},
        {"windowFocused": False},
        {"windowMinimized": True},
        {"sessionState": "locked"},
        {"sessionState": "unknown"},
        {"currentSessionId": "other"},
        {"observedAt": "2020-01-01T00:00:00Z"},
    ],
)
def test_background_or_unreliable_presence_cannot_mark_content_shown(
    setup: tuple[TestClient, MessageExposureService, JsonlSessionStore], override: dict[str, Any]
) -> None:
    client, service, _ = setup
    payload = _payload()
    payload.update(override)
    assert client.post("/v1/runtime/presence/desktop", json=payload).status_code == 200
    assert service.store.changes(0)["items"] == []


def _payload() -> dict[str, Any]:
    return {
        "observedAt": datetime.now(UTC).isoformat(),
        "currentSessionId": "s",
        "windowVisible": True,
        "windowFocused": True,
        "windowMinimized": False,
        "sessionState": "active",
        "messageObservations": [
            {
                "messageId": "a",
                "contentVersion": content_version("正文"),
                "extent": "full",
                "dwellMs": 2000,
            }
        ],
    }


def test_version_checked_display_is_separate_from_chat_and_read_receipt(
    setup: tuple[TestClient, MessageExposureService, JsonlSessionStore],
) -> None:
    client, service, store = setup
    before = store.load("s").events
    bad = _payload()
    bad["messageObservations"][0]["contentVersion"] = content_version("另一版")
    assert client.post("/v1/runtime/presence/desktop", json=bad).status_code == 200
    assert service.changes(0)["items"] == []
    assert client.post("/v1/runtime/presence/desktop", json=_payload()).status_code == 200
    evidence = service.evidence("s", before[-1])
    assert evidence["desktop"]["extent"] == "full"
    assert evidence["desktop"]["read_status"] == evidence["im_read_status"] == "unknown"
    assert store.load("s").events == before
    assert len(service.changes(0)["items"]) == 1
    store.delete("s")
    assert service.changes(0)["items"] == []
    assert service.store.changes(0)["items"] == []
