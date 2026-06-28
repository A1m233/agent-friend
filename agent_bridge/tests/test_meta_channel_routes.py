"""007 起 agent_bridge 新增 ``POST /v1/sessions`` + ``POST /v1/sessions/{id}/channel``
两个端点的集成测试。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    SessionNotFoundError,
    make_default_registry,
)

if TYPE_CHECKING:
    from llm_providers import LLMClient

from llm_providers import LLMStreamEvent, LLMTextDelta, LLMTurnDone


class _RouteLLM:
    context_window = 128000

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:  # pragma: no cover
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        yield LLMTextDelta(text="new answer")
        yield LLMTurnDone(stop_reason="end_turn")


@pytest.fixture
def runtime(tmp_path: Path) -> BridgeRuntime:
    """构造一个全新 BridgeRuntime，sessions 落到 tmp_path 隔离。"""
    settings = BridgeSettings(sessions_dir=tmp_path / "sessions")
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()

    def _llm_factory(model: str) -> LLMClient:
        return cast("LLMClient", _RouteLLM())

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    persistent_session_manager = SessionManager(
        store=persistent_store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
    )

    return BridgeRuntime(
        settings=settings,
        persistent_store=persistent_store,
        transient_store=persistent_store,  # 测试用同一个就行
        persistent_session_manager=persistent_session_manager,
        catalog=catalog,
        tool_registry=tool_registry,
        prompt_builder_factory=_prompt_factory,
        default_persona=catalog.find_by_name("default").name,
        default_model="deepseek/deepseek-chat",
    )


@pytest.fixture
def client(runtime: BridgeRuntime) -> TestClient:
    app = create_app_with_runtime(runtime)
    return TestClient(app)


# ===== POST /v1/sessions =====


class TestCreateSessionEndpoint:
    def test_default_creates_text_channel(self, client: TestClient) -> None:
        resp = client.post("/v1/sessions", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"]
        assert data["channel"] == "text"
        assert data["persona"]
        assert data["model"]

    def test_voice_channel_persists_to_session_meta(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        resp = client.post("/v1/sessions", json={"channel": "voice"})
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        # 直接通过 store 重新读 session，确认 initial_channel 落盘
        loaded = runtime.persistent_session_manager.open(sid)
        assert loaded.current_channel == "voice"
        assert loaded.events[0].payload.get("initial_channel") == "voice"

    def test_persona_argument(self, client: TestClient) -> None:
        resp = client.post("/v1/sessions", json={"persona": "default"})
        assert resp.status_code == 200
        assert resp.json()["persona"]

    def test_unknown_persona_returns_400(self, client: TestClient) -> None:
        resp = client.post("/v1/sessions", json={"persona": "totally_made_up"})
        assert resp.status_code == 400


# ===== DELETE /v1/sessions/{id} =====


class TestDeleteSessionEndpoint:
    def test_delete_session_removes_file(self, client: TestClient, runtime: BridgeRuntime) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        resp = client.delete(f"/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": sid, "deleted": True}

        with pytest.raises(SessionNotFoundError, match="会话不存在"):
            runtime.persistent_session_manager.open(sid)

    def test_delete_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.delete("/v1/sessions/nonexistent-uuid")
        assert resp.status_code == 404


# ===== POST /v1/sessions/{id}/edit-resend-latest =====


class TestEditResendLatestEndpoint:
    def test_edit_resend_latest_streams_and_exposes_active_events(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        old_user = Event(
            type="user_message",
            uuid="old-user",
            ts=datetime.now(UTC),
            payload={"content": "old question"},
        )
        old_assistant = Event(
            type="assistant_message",
            uuid="old-assistant",
            ts=datetime.now(UTC),
            payload={"content": "old answer", "partial": False},
        )
        runtime.persistent_store.append_event(sid, old_user)
        runtime.persistent_store.append_event(sid, old_assistant)

        with client.stream(
            "POST",
            f"/v1/sessions/{sid}/edit-resend-latest",
            json={"text": "new question", "expectedUserContent": "old question"},
            headers={"accept": "text/event-stream"},
        ) as resp:
            body = "".join(resp.iter_text())

        assert resp.status_code == 200
        assert "new answer" in body

        detail = client.get(f"/v1/sessions/{sid}").json()
        assert [ev["type"] for ev in detail["events"]].count("turn_rewrite") == 1
        active_message_contents = [
            ev["payload"].get("content")
            for ev in detail["active_events"]
            if ev["type"] in {"user_message", "assistant_message"}
        ]
        assert active_message_contents == [
            "new question",
            "new answer",
        ]


# ===== POST /v1/sessions/{id}/channel =====


class TestSwitchChannelEndpoint:
    def test_switch_text_to_voice(self, client: TestClient, runtime: BridgeRuntime) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        resp = client.post(f"/v1/sessions/{sid}/channel", json={"channel": "voice"})
        assert resp.status_code == 200
        assert resp.json()["channel"] == "voice"

        # 验证落盘了 channel_change 事件
        loaded = runtime.persistent_session_manager.open(sid)
        change_events = [e for e in loaded.events if e.type == "channel_change"]
        assert len(change_events) == 1
        assert change_events[0].payload["to"] == "voice"
        assert change_events[0].payload["from"] == "text"

    def test_switch_idempotent_no_event(self, client: TestClient, runtime: BridgeRuntime) -> None:
        """切换到当前 channel 不应再写事件。"""
        sid = client.post("/v1/sessions", json={"channel": "voice"}).json()["session_id"]
        before = runtime.persistent_session_manager.open(sid)
        before_count = len(before.events)
        resp = client.post(f"/v1/sessions/{sid}/channel", json={"channel": "voice"})
        assert resp.status_code == 200
        after = runtime.persistent_session_manager.open(sid)
        assert len(after.events) == before_count

    def test_switch_unknown_session_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/sessions/nonexistent-uuid/channel",
            json={"channel": "voice"},
        )
        assert resp.status_code == 404

    def test_invalid_channel_value_returns_422(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        resp = client.post(f"/v1/sessions/{sid}/channel", json={"channel": "garbage"})
        # pydantic Literal 校验失败 → 422
        assert resp.status_code == 422

    def test_switch_back_appends_another_event(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        client.post(f"/v1/sessions/{sid}/channel", json={"channel": "voice"})
        client.post(f"/v1/sessions/{sid}/channel", json={"channel": "text"})
        loaded = runtime.persistent_session_manager.open(sid)
        change_events = [e for e in loaded.events if e.type == "channel_change"]
        assert len(change_events) == 2
        assert change_events[0].payload["to"] == "voice"
        assert change_events[1].payload["to"] == "text"
        assert loaded.current_channel == "text"
