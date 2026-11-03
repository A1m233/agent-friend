from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from agent_bridge.assembly import build_runtime
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from agent_bridge.settings import BridgeSettings

from agent import AgentError
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
)
from runtime_config import DesktopSettingsReader, credential_source_for_mode


class CompanionModel:
    context_window = 128000

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.target = ""

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse('{"outcome":"allow"}')

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield LLMToolCallDelta(
                index=0,
                tool_call_id="prepared",
                tool_name="send_conversation_message",
                args_json_delta=json.dumps(
                    {
                        "target_session_id": self.target,
                        "content": "找到一件你可能感兴趣的小事。",
                    }
                ),
            )
            yield LLMTurnDone(stop_reason="tool_use")
        else:
            yield LLMTextDelta(text="准备好了，这次先到这里。")
            yield LLMTurnDone(stop_reason="end_turn")


def test_real_runtime_reuses_inner_and_delivers_without_target_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_FRIEND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_FRIEND_LOG_DIR", str(tmp_path / "logs"))
    model = CompanionModel()
    config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    config = replace(config, llm_client_factory=lambda _: model)
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {"authorizationMode": "auto_review", "chatModel": {"model": config.default_model}}
        ),
        encoding="utf-8",
    )
    runtime = build_runtime(
        BridgeSettings(
            sessions_dir=tmp_path / "data" / "sessions",
            personas_dir=tmp_path / "data" / "personas",
            memory_enabled=False,
            im_enabled=False,
        ),
        runtime_config=config,
        desktop_settings_reader=DesktopSettingsReader(path),
    )
    service = runtime.companionship
    assert (
        service is not None
        and runtime.agent_runtime is not None
        and runtime.session_delivery is not None
    )
    manager = runtime.persistent_session_manager
    persona = runtime.catalog.find_by_name(runtime.default_persona)
    target = manager.create(persona.name, config.default_model, persona_id=persona.id, title="聊天")
    model.target = target.session_id
    try:
        runtime.agent_runtime.start()
        state = service.control("enable", user_initiated=True)
        inner = state["inner_session_id"]
        broker = runtime.approval_broker
        assert broker is not None
        assert broker.mode_state(inner).mode == "auto_review"
        assert broker.mode_state(target.session_id).mode == "per_request"
        desktop_settings = json.loads(path.read_text(encoding="utf-8"))
        desktop_settings.update(authorizationMode="full_access", authorizationPolicyVersion=1)
        path.write_text(json.dumps(desktop_settings), encoding="utf-8")
        assert broker.mode_state(inner).mode == "full_access"
        assert broker.mode_state(target.session_id).mode == "per_request"
        broker.set_session_mode(target.session_id, mode="auto_review", expected_version=0)
        assert manager.approval_mode_state(manager.open(target.session_id)).mode == "auto_review"
        assert broker.mode_state(inner).mode == "full_access"
        service.store.update(lambda state: state.update(next_wake_at=None))
        service.tick()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = service.status()
            if current["last_outcome"] is not None and not current["active_run_id"]:
                break
            time.sleep(0.05)
        assert service.status()["last_outcome"] == "completed"
        assert service.status()["today_model_calls"] == 2
        assert service.store.read()["last_overview_at"] is not None
        runtime.session_delivery.deliver_pending()
        delivered = manager.open(target.session_id)
        assert [
            event.payload["content"]
            for event in delivered.events
            if event.type == "assistant_message"
        ] == ["找到一件你可能感兴趣的小事。"]
        assert not any(event.type == "user_message" for event in delivered.events)
        assert len(model.requests) == 2
        assert any(
            '"last_desktop_interaction_at": null' in str(message.get("content"))
            for message in model.requests[0].messages
        )
        assert runtime.notification_service is not None
        notifications = runtime.notification_service.store.list()
        assert len(notifications) == 1
        assert notifications[0].fact.session_id == target.session_id
        assert notifications[0].text == "找到一件你可能感兴趣的小事。"
        runtime.session_delivery.deliver_pending()
        assert len(runtime.notification_service.store.list()) == 1
        assert inner not in {item.session_id for item in manager.list()}
        assert service.ensure_inner() == inner
        with pytest.raises(AgentError, match="只读"):
            manager.start_conversation(manager.open(inner))
        assert service.tick() is None
        service.control("pause_contact", user_initiated=True)
        assert not service.contact_allowed(inner)
        with pytest.raises(ValueError, match="当前请求"):
            service.control("resume_contact", user_initiated=False)
    finally:
        runtime.agent_runtime.stop()
        runtime.close()

    # Simulate two days of missed opportunities followed by a new desktop bridge.
    from datetime import timedelta

    service.store.update(
        lambda data: data.update(
            next_wake_at=(runtime.time_provider.now() - timedelta(days=2)).isoformat()
        )
    )
    restarted = build_runtime(
        runtime.settings, runtime_config=config, desktop_settings_reader=DesktopSettingsReader(path)
    )
    resumed = restarted.companionship
    assert resumed and restarted.agent_runtime and restarted.session_delivery
    try:
        assert resumed.ensure_inner() == inner
        assert resumed.status()["contact_paused"]
        restarted.agent_runtime.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and (
            len(model.requests) < 3 or resumed.status()["active_run_id"]
        ):
            time.sleep(0.05)
        assert len(model.requests) == 3
        assert resumed.status()["last_outcome"] == "completed"
        assert all(resumed.tick() is None for _ in range(3))
        restarted.session_delivery.deliver_pending()
        target_events = restarted.persistent_session_manager.open(target.session_id).events
        assert sum(e.type == "assistant_message" for e in target_events) == 1
    finally:
        restarted.agent_runtime.stop()
        restarted.close()
