from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent.approvals.reviewer import REVIEW_CONTEXT_ARG, ReviewContext, visible_review_transcript
from agent.filesystem.models import FilesystemAuthorizationLedger
from agent.filesystem.tools import (
    FILESYSTEM_LEDGER_ARG,
    FILESYSTEM_RUN_ID_ARG,
    FILESYSTEM_SESSION_ID_ARG,
    FILESYSTEM_SURFACE_ARG,
    FILESYSTEM_TOOL_CALL_ID_ARG,
)
from agent_bridge.assembly import BridgeRuntime, build_runtime
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from agent_bridge.settings import BridgeSettings

from agent import AgentError, Event
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
                        **({"target_session_id": self.target} if self.target else {}),
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
        assert "这是你第一次自主醒来" in str(model.requests[0].messages)
        assert "This surface cannot grant" not in str(model.requests[0].messages)
        assert "Use request_filesystem_access for the exact file" in str(model.requests[0].messages)
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
        assert "这是你第一次自主醒来" not in str(model.requests[2].messages)
        assert len(model.requests) == 3
        assert resumed.status()["last_outcome"] == "completed"
        assert all(resumed.tick() is None for _ in range(3))
        restarted.session_delivery.deliver_pending()
        target_events = restarted.persistent_session_manager.open(target.session_id).events
        assert sum(e.type == "assistant_message" for e in target_events) == 1
    finally:
        restarted.agent_runtime.stop()
        restarted.close()


def isolated_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: CompanionModel
) -> BridgeRuntime:
    monkeypatch.setenv("AGENT_FRIEND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_FRIEND_LOG_DIR", str(tmp_path / "logs"))
    settings = tmp_path / "settings.json"
    settings.write_text('{"authorizationMode":"auto_review"}', encoding="utf8")
    config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    return build_runtime(
        BridgeSettings(
            sessions_dir=tmp_path / "data" / "sessions",
            personas_dir=tmp_path / "data" / "personas",
            memory_enabled=False,
            im_enabled=False,
        ),
        runtime_config=replace(config, llm_client_factory=lambda _: model),
        desktop_settings_reader=DesktopSettingsReader(settings),
    )


@pytest.mark.parametrize("scenario", ["empty", "concurrent", "archived", "paused", "retry"])
def test_intro_routing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str) -> None:
    runtime = isolated_runtime(tmp_path, monkeypatch, CompanionModel())
    service = runtime.companionship
    delivery = runtime.session_delivery
    assert service and delivery
    manager = runtime.persistent_session_manager
    try:
        state = service.control("enable", user_initiated=True)
        inner = state["inner_session_id"]
        run = "test-wake"
        snapshot = {"wake_id": run, "introduce": True}
        service.store.update(lambda s: s.update(next_wake_at=None))
        assert service.store.begin(run, snapshot, now=runtime.time_provider.now())
        args = {
            "__agent_friend_current_session_id": inner,
            "__agent_friend_run_id": run,
            "__agent_friend_tool_call_id": "a",
            "content": "嗨，我也会自己写写故事。",
        }
        receipt = delivery.submit(args)
        assert manager.list() == []
        if scenario in {"concurrent", "archived"}:
            target = manager.create(runtime.default_persona, runtime.default_model)
            if scenario == "archived":
                manager.archive(target.session_id)
        if scenario == "paused":
            service.control("pause_contact", user_initiated=True)
        if scenario == "retry":

            def interrupt(*_args: object) -> None:
                raise OSError("worker interrupted after publishing")

            with monkeypatch.context() as patch:
                patch.setattr(delivery.store, "bind_first_target", interrupt)
                with pytest.raises(OSError, match="worker interrupted"):
                    delivery.deliver_pending()
            assert len(manager.list()) == 1
        delivery.deliver_pending()
        row = delivery.store.get(receipt["delivery_id"])
        if scenario in {"archived", "paused"}:
            assert row["conversation_state"] == "cancelled"
            assert manager.list() == []
            return
        assert row["conversation_state"] == "committed"
        assert row["im_state"] == "not_requested"
        assert (
            delivery.submit({**args, "__agent_friend_tool_call_id": "b"})["delivery_id"]
            == receipt["delivery_id"]
        )
        delivery.deliver_pending()
        chats = manager.list()
        assert len(chats) == 1
        if scenario == "concurrent":
            assert chats[0].session_id == target.session_id
        session = manager.open_for_execution(chats[0].session_id)
        assert [e.payload["content"] for e in session.events if e.type == "assistant_message"] == [
            args["content"]
        ]
        conv = manager.start_conversation(session)
        assert conv.session.session_id == chats[0].session_id
        if scenario == "empty":
            list(conv.stream("写点故事，想摸鱼就摸鱼。"))
            assert any(
                e.type == "user_message" and e.payload.get("content") == "写点故事，想摸鱼就摸鱼。"
                for e in manager.open(chats[0].session_id).events
            )
    finally:
        runtime.close()


def test_inner_wake_resumes_same_notebook_across_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = isolated_runtime(tmp_path, monkeypatch, CompanionModel())

    def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = runtime.tool_registry.get(name).invoke(args)
        assert not result.is_error, result.text
        return dict(json.loads(result.text))

    try:
        assert runtime.companionship
        inner = runtime.companionship.ensure_inner()
        drafts = tmp_path / "草稿"
        drafts.mkdir()
        notebook = drafts / "手记.md"
        ordinary = runtime.persistent_session_manager.create(
            runtime.default_persona,
            runtime.default_model,
            initial_events=(
                Event(
                    type="user_message",
                    uuid="u1",
                    ts=datetime.now(UTC),
                    payload={"content": "帮我存一份手记"},
                ),
                Event(
                    type="assistant_message",
                    uuid="a1",
                    ts=datetime.now(UTC),
                    payload={"content": f"手记保存在 {notebook}"},
                ),
                Event(
                    type="user_message",
                    uuid="u2",
                    ts=datetime.now(UTC),
                    payload={"content": "以后都直接写到文档里"},
                ),
            ),
        )
        broker = runtime.approval_broker
        assert broker
        broker.set_session_mode(ordinary.session_id, mode="auto_review", expected_version=0)
        writer = {
            FILESYSTEM_SESSION_ID_ARG: ordinary.session_id,
            FILESYSTEM_RUN_ID_ARG: "create",
            FILESYSTEM_TOOL_CALL_ID_ARG: "scope",
            FILESYSTEM_SURFACE_ARG: "desktop_chat",
            FILESYSTEM_LEDGER_ARG: FilesystemAuthorizationLedger(),
            REVIEW_CONTEXT_ARG: ReviewContext(
                CompanionModel(), visible_review_transcript(ordinary.events)
            ),
        }
        approved = call(
            "request_filesystem_access",
            {
                **writer,
                "reason": "保存手记",
                "items": [
                    {
                        "item_id": "drafts",
                        "locator": {"kind": "path", "path": str(drafts)},
                        "root_kind": "directory",
                        "permissions": ["create"],
                    }
                ],
            },
        )
        call(
            "filesystem_write_text",
            {
                **writer,
                "filesystem_scope_id": approved["scopes"][0]["filesystem_scope_id"],
                "relative_path": notebook.name,
                "content": "街道旧稿\n",
            },
        )
        messages = call(
            "read_conversation",
            {
                "__agent_friend_current_session_id": inner,
                "session_id": ordinary.session_id,
                "mode": "latest",
                "limit": 3,
            },
        )["messages"]
        path = next(
            m["text"].removeprefix("手记保存在 ") for m in messages if m["message_id"] == "a1"
        )
        for run, permissions in (("write", ["read_text", "edit"]), ("verify", ["read_text"])):
            context = {
                FILESYSTEM_SESSION_ID_ARG: inner,
                FILESYSTEM_RUN_ID_ARG: run,
                FILESYSTEM_TOOL_CALL_ID_ARG: "scope",
                FILESYSTEM_LEDGER_ARG: FilesystemAuthorizationLedger(),
                FILESYSTEM_SURFACE_ARG: "",
                REVIEW_CONTEXT_ARG: ReviewContext(
                    CompanionModel(), [{"event_type": "tool_call_result", "content": messages}]
                ),
            }
            grant = call(
                "request_filesystem_access",
                {
                    **context,
                    "reason": "按用户要求续写原手记",
                    "items": [
                        {
                            "item_id": "手记",
                            "root_kind": "file",
                            "locator": {"kind": "path", "path": path},
                            "permissions": permissions,
                        }
                    ],
                },
            )
            file = {
                **context,
                "filesystem_scope_id": grant["scopes"][0]["filesystem_scope_id"],
                "relative_path": ".",
            }
            page = call("filesystem_read_text", file)
            if run == "write":
                assert page["content"].strip() == "街道旧稿"
                call(
                    "filesystem_apply_patch",
                    {
                        **file,
                        "expected_sha256": page["sha256"],
                        "patch": "*** Begin Patch\n@@\n 街道旧稿\n+面包店老板递出纸袋。\n*** End Patch",
                    },
                )
            else:
                assert page["content"].splitlines() == ["街道旧稿", "面包店老板递出纸袋。"]
        assert notebook.read_text(encoding="utf8").splitlines() == [
            "街道旧稿",
            "面包店老板递出纸袋。",
        ]
    finally:
        runtime.close()


def test_first_wake_while_paused_does_not_introduce_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = CompanionModel()
    runtime = isolated_runtime(tmp_path, monkeypatch, model)
    service = runtime.companionship
    assert service and runtime.agent_runtime and runtime.session_delivery
    try:
        service.control("enable", user_initiated=True)
        service.control("pause_contact", user_initiated=True)
        # The test advances wake opportunities itself, independently of the polling thread.
        monkeypatch.setattr(service, "_loop", lambda: None)
        runtime.agent_runtime.start()
        for paused in (True, False):
            if not paused:
                service.control("resume_contact", user_initiated=True)
            service.store.update(lambda s: s.update(next_wake_at=None))
            assert service.tick()
            deadline = time.monotonic() + 20
            while service.store.read()["active_run_id"] and time.monotonic() < deadline:
                time.sleep(0.05)
            assert service.status()["last_outcome"] == "completed"
            runtime.session_delivery.deliver_pending()
            assert runtime.persistent_session_manager.list() == []
        assert model.requests
        assert all(
            "这是你第一次自主醒来" not in str(request.messages) for request in model.requests
        )
    finally:
        runtime.agent_runtime.stop()
        runtime.close()
