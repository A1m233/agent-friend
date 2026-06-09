"""007 起 agent_bridge 新增 ``POST /v1/sessions`` + ``POST /v1/sessions/{id}/channel``
两个端点的集成测试。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    make_default_registry,
)

if TYPE_CHECKING:
    from llm_providers import LLMClient


@pytest.fixture
def runtime(tmp_path: Path) -> BridgeRuntime:
    """构造一个全新 BridgeRuntime，sessions 落到 tmp_path 隔离。"""
    settings = BridgeSettings(sessions_dir=tmp_path / "sessions")
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()

    def _llm_factory(model: str) -> LLMClient:
        # 测试不发消息，所以这里不需要真实 LLMClient——但保险起见用最小 spec
        from llm_providers import LLMClient, ProviderSpec

        return LLMClient(ProviderSpec(model=model, api_key="sk-test"))

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
