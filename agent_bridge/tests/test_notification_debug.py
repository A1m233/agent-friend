"""Development bubble examples use real delivery receipts without model or memory writes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from agent.notifications import NotificationFact, NotificationRecord, NotificationStore
from agent.personas import BUILTIN_DEFAULT_PERSONA_ID
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.notifications.generation import NotificationGenerator
from agent_bridge.notifications.service import NotificationService
from agent_bridge.presence import PresenceRegistry
from agent_bridge.routes.notifications import DismissRequest, register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import JsonlSessionStore
from memory import Memory
from runtime_config import DesktopSettingsReader

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def build(
    tmp_path: Path, *, dev_mode: bool = True
) -> tuple[NotificationService, Mock, list[dict[str, Any]], TestClient]:
    reader = DesktopSettingsReader(tmp_path / "settings.json")
    reader.path.write_text("{}", encoding="utf-8")
    generator = Mock(spec=NotificationGenerator)
    generator.generate.side_effect = AssertionError("debug notification must not call a model")
    published: list[dict[str, Any]] = []
    manager = NotificationService(
        store=NotificationStore(tmp_path / "notifications.db"),
        sessions=JsonlSessionStore(tmp_path / "sessions"),
        presence=PresenceRegistry(),
        reader=reader,
        generator=cast(NotificationGenerator, generator),
        publisher=published.append,
        clock=lambda: NOW,
    )
    app = FastAPI()
    register_routes(
        app,
        cast(
            BridgeRuntime,
            SimpleNamespace(
                notification_service=manager, settings=SimpleNamespace(dev_mode=dev_mode)
            ),
        ),
    )
    return manager, generator, published, TestClient(app)


def present(client: TestClient, item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    path = f"/v1/notifications/{item['notificationId']}"
    prepared = client.post(
        path + "/prepare", json={"version": item["version"], "windowId": "debug-bubble-window"}
    )
    assert prepared.status_code == 200
    value = prepared.json()
    body = {"attempt": value["attempt"], "text": value["text"], "truncated": False}
    first = client.post(path + "/presented", json=body)
    assert first.status_code == 200
    duplicate = client.post(path + "/presented", json=body)
    assert duplicate.status_code == 200
    return first.json(), duplicate.json()


def test_debug_routes_are_absent_when_dev_mode_is_disabled(tmp_path: Path) -> None:
    manager, generator, published, client = build(tmp_path, dev_mode=False)
    assert client.post("/v1/notifications/debug", json={"scenario": "short"}).status_code == 404
    assert client.delete("/v1/notifications/debug").status_code == 404
    assert "/v1/notifications/debug" not in client.get("/openapi.json").json()["paths"]
    assert client.get("/v1/notifications").status_code == 200
    assert manager.store.list() == []
    assert published == []
    generator.generate.assert_not_called()


@pytest.mark.parametrize("scenario,count", [("short", 1), ("long", 1), ("stack", 5)])
def test_debug_scenarios_publish_fixed_ready_content_without_generation(
    tmp_path: Path, scenario: str, count: int
) -> None:
    manager, generator, published, client = build(tmp_path)
    response = client.post("/v1/notifications/debug", json={"scenario": scenario})
    assert response.status_code == 200
    items = response.json()["notifications"]
    assert len(items) == count
    assert len({item["notificationId"] for item in items}) == count
    assert len(published) == count
    for item in items:
        assert item["eventKey"].startswith("debug:")
        assert item["kind"] == "ambient"
        assert item["title"] == "气泡调试"
        assert item["generationStatus"] == "ready"
        assert item["textSource"] == "provided"
        assert item["delivery"]["status"] == "pending"
        assert item["navigation"] is None
        assert item["sessionId"] is None
        stored = manager.store.get(item["notificationId"])
        assert stored.fact.persona_id == BUILTIN_DEFAULT_PERSONA_ID
        assert stored.fact.run_id is None
        assert stored.fact.approval_id is None
    if scenario == "long":
        assert 150 <= len(items[0]["text"]) <= 160
        assert items[0]["text"].endswith("长通知到这里完整结束。")
    assert not manager.run_once()
    generator.generate.assert_not_called()
    another = client.post("/v1/notifications/debug", json={"scenario": "short"})
    assert another.json()["notifications"][0]["notificationId"] not in {
        item["notificationId"] for item in items
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"scenario": "custom"},
        {"scenario": "short", "text": "arbitrary text"},
        {"scenario": "short", "sessionId": "real-session"},
        {"scenario": "short", "eventKey": "run:forged"},
    ],
)
def test_debug_request_cannot_supply_text_or_identity(tmp_path: Path, body: dict[str, Any]) -> None:
    manager, generator, published, client = build(tmp_path)
    assert client.post("/v1/notifications/debug", json=body).status_code == 422
    assert manager.store.list() == []
    assert published == []
    generator.generate.assert_not_called()


@pytest.mark.parametrize("memory_enabled", [True, False])
def test_debug_presented_receipts_keep_normal_sound_semantics_but_discard_memory(
    tmp_path: Path, memory_enabled: bool
) -> None:
    manager, generator, published, client = build(tmp_path)
    memory = Mock(spec=Memory)
    manager.memory = cast(Memory, memory) if memory_enabled else None
    item = client.post("/v1/notifications/debug", json={"scenario": "short"}).json()[
        "notifications"
    ][0]
    first, duplicate = present(client, item)
    assert first["playSound"] is True
    assert duplicate["playSound"] is False
    assert first["notification"]["delivery"]["status"] == "presented"
    assert not manager.run_once()
    assert not manager.run_once()
    stored = manager.store.get(item["notificationId"])
    assert stored.delivery["memoryStatus"] == "discarded"
    assert stored.delivery["presentedText"] == item["text"]
    assert len(published) >= 3
    memory.record_host_episode.assert_not_called()
    generator.generate.assert_not_called()


def test_real_ambient_notification_still_projects_memory(tmp_path: Path) -> None:
    manager, generator, _published, _client = build(tmp_path)
    memory = Mock(spec=Memory)
    memory.record_host_episode.return_value = "inserted"
    manager.memory = cast(Memory, memory)
    item = manager.accept(
        NotificationFact(
            event_key="run:debug:real",
            kind="ambient",
            title="真实提醒",
            persona_id="p1",
            created_at=NOW,
        ),
        provided_text="该休息一下啦。",
    )
    prepared = manager.prepare(item.notification_id, version=item.version, window_id="bubble")
    manager.presented(
        item.notification_id, attempt=prepared.delivery["attempt"], text=item.text, truncated=False
    )
    assert not manager.run_once()
    memory.record_host_episode.assert_called_once()
    assert manager.store.get(item.notification_id).delivery["memoryStatus"] == "projected"
    generator.generate.assert_not_called()


def test_dismiss_request_deduplicates_bounded_ids_and_keeps_legacy_default() -> None:
    assert DismissRequest(version=1).silent_replacement_ids == []
    assert DismissRequest.model_validate(
        {"version": 1, "silentReplacementIds": ["a", "b" * 256, "a", "b" * 256]}
    ).silent_replacement_ids == ["a", "b" * 256]


@pytest.mark.parametrize(
    "extra",
    [
        {"silentReplacementIds": [""]},
        {"silentReplacementIds": ["x" * 257]},
        {"silentReplacementIds": ["same"] * 5},
        {"silentReplacementIds": [1]},
        {"silentReplacementIds": "notification-id"},
        {"silentReplacementIds": None},
        {"silent_replacement_ids": ["notification-id"]},
    ],
)
def test_invalid_silent_replacement_request_cannot_close_notification(
    tmp_path: Path, extra: dict[str, Any]
) -> None:
    manager, generator, _published, client = build(tmp_path)
    item = client.post("/v1/notifications/debug", json={"scenario": "short"}).json()[
        "notifications"
    ][0]
    before = manager.store.get(item["notificationId"])
    response = client.post(
        f"/v1/notifications/{item['notificationId']}/dismiss",
        json={"version": item["version"], **extra},
    )
    assert response.status_code == 422
    assert manager.store.get(item["notificationId"]) == before
    generator.generate.assert_not_called()


def test_explicit_close_replacement_is_silent_but_still_projects_its_presented_memory(
    tmp_path: Path,
) -> None:
    manager, generator, _published, client = build(tmp_path)
    memory = Mock(spec=Memory)
    memory.record_host_episode.return_value = "inserted"
    manager.memory = cast(Memory, memory)
    closed = client.post("/v1/notifications/debug", json={"scenario": "short"}).json()[
        "notifications"
    ][0]
    replacement = manager.accept(
        NotificationFact(
            event_key="ambient:replacement",
            kind="ambient",
            title="真实补位提醒",
            persona_id="p1",
            created_at=NOW,
        ),
        provided_text="补位提醒依然展示并记录呈现。",
    )
    payload = {
        "version": closed["version"],
        "silentReplacementIds": [replacement.notification_id, replacement.notification_id],
    }
    url = f"/v1/notifications/{closed['notificationId']}/dismiss"
    first_close = client.post(url, json=payload)
    assert first_close.status_code == 200
    assert client.post(url, json=payload).json() == first_close.json()
    assert manager.store.get(replacement.notification_id).version == replacement.version
    first, duplicate = present(client, replacement.to_wire())
    assert first["playSound"] is False
    assert duplicate["playSound"] is False
    assert first["notification"]["delivery"]["status"] == "presented"
    assert first["notification"]["delivery"]["presentedText"] == replacement.text
    assert not manager.run_once()
    assert not manager.run_once()
    memory.record_host_episode.assert_called_once()
    assert (
        memory.record_host_episode.call_args.args[0].notification_id == replacement.notification_id
    )
    assert manager.store.get(replacement.notification_id).delivery["memoryStatus"] == "projected"

    newly_arrived = client.post("/v1/notifications/debug", json={"scenario": "short"}).json()[
        "notifications"
    ][0]
    assert present(client, newly_arrived)[0]["playSound"] is True
    generator.generate.assert_not_called()


def test_debug_clear_is_idempotent_and_preserves_real_notifications(tmp_path: Path) -> None:
    manager, generator, published, client = build(tmp_path)
    real = manager.accept(
        NotificationFact(
            event_key="run:debug:real",
            kind="ambient",
            title="真实提醒",
            persona_id="p1",
            created_at=NOW,
        ),
        provided_text="这是真实通知，不应该被清空。",
    )
    items = client.post("/v1/notifications/debug", json={"scenario": "stack"}).json()[
        "notifications"
    ]
    present(client, items[0])
    published.clear()
    response = client.delete("/v1/notifications/debug")
    assert response.status_code == 200
    snapshot = {item["notificationId"]: item for item in response.json()["notifications"]}
    assert snapshot[real.notification_id] == real.to_wire()
    assert manager.store.get(real.notification_id) == real
    assert all(
        snapshot[item["notificationId"]]["delivery"]["status"] == "dismissed" for item in items
    )
    assert {item["notificationId"] for item in published} == {
        item["notificationId"] for item in items
    }
    again = client.delete("/v1/notifications/debug")
    assert again.status_code == 200
    assert again.json() == response.json()
    assert len(published) == len(items)
    generator.generate.assert_not_called()


def test_debug_clear_retries_when_preparation_advances_its_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _generator, _published, client = build(tmp_path)
    item = client.post("/v1/notifications/debug", json={"scenario": "short"}).json()[
        "notifications"
    ][0]
    original_dismiss = manager.dismiss
    attempts: list[int] = []

    def race(notification_id: str, *, version: int) -> NotificationRecord:
        attempts.append(version)
        if len(attempts) == 1:
            manager.prepare(notification_id, version=version, window_id="new-bubble")
        return original_dismiss(notification_id, version=version)

    monkeypatch.setattr(manager, "dismiss", race)
    response = client.delete("/v1/notifications/debug")
    assert response.status_code == 200
    assert attempts == [1, 2]
    assert manager.store.get(item["notificationId"]).delivery["status"] == "dismissed"
