"""007 起 agent_bridge 新增 ``POST /v1/sessions`` + ``POST /v1/sessions/{id}/channel``
两个端点的集成测试。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

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
    SessionMetadataStore,
    SessionNotFoundError,
    make_default_registry,
)

if TYPE_CHECKING:
    from llm_providers import LLMClient

from llm_providers import LLMStreamEvent, LLMTextDelta, LLMTurnDone


class _RouteLLM:
    seen_messages: ClassVar[list[list[dict[str, Any]]]] = []
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
        self.seen_messages.append(messages)
        yield LLMTextDelta(text="new answer")
        yield LLMTurnDone(stop_reason="end_turn")


@pytest.fixture
def runtime(tmp_path: Path) -> BridgeRuntime:
    """构造一个全新 BridgeRuntime，sessions 落到 tmp_path 隔离。"""
    _RouteLLM.seen_messages = []
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
        metadata_store=SessionMetadataStore(settings.sessions_dir / "metadata.json"),
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


# ===== PATCH /v1/sessions/{id} + archived listing/deletion =====


class TestSessionLifecycleEndpoint:
    def test_rename_overrides_list_title(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.patch(f"/v1/sessions/{sid}", json={"title": "新的标题"})

        assert resp.status_code == 200
        assert resp.json()["title"] == "新的标题"
        items = client.get("/v1/sessions").json()
        assert items[0]["session_id"] == sid
        assert items[0]["title"] == "新的标题"
        assert items[0]["archived_at"] is None

    def test_rename_empty_title_returns_400(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.patch(f"/v1/sessions/{sid}", json={"title": "   "})

        assert resp.status_code == 400

    def test_archive_moves_session_to_archived_list(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        resp = client.patch(f"/v1/sessions/{sid}", json={"archived": True})

        assert resp.status_code == 200
        assert resp.json()["archived_at"]
        assert client.get("/v1/sessions").json() == []
        archived = client.get("/v1/sessions?archived=true").json()
        assert [item["session_id"] for item in archived] == [sid]

    def test_unarchive_returns_session_to_default_list(self, client: TestClient) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]
        client.patch(f"/v1/sessions/{sid}", json={"archived": True})

        resp = client.patch(f"/v1/sessions/{sid}", json={"archived": False})

        assert resp.status_code == 200
        assert resp.json()["archived_at"] is None
        assert client.get("/v1/sessions?archived=true").json() == []
        assert [item["session_id"] for item in client.get("/v1/sessions").json()] == [sid]

    def test_archived_search_runs_on_backend_title(self, client: TestClient) -> None:
        first = client.post("/v1/sessions", json={}).json()["session_id"]
        second = client.post("/v1/sessions", json={}).json()["session_id"]
        client.patch(f"/v1/sessions/{first}", json={"title": "查 Codex 5.5 缓存时长"})
        client.patch(f"/v1/sessions/{first}", json={"archived": True})
        client.patch(f"/v1/sessions/{second}", json={"title": "别的聊天"})
        client.patch(f"/v1/sessions/{second}", json={"archived": True})

        resp = client.get("/v1/sessions?archived=true&q=codex")

        assert resp.status_code == 200
        assert [item["session_id"] for item in resp.json()] == [first]

    def test_delete_archived_removes_only_archived_sessions(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        archived_sid = client.post("/v1/sessions", json={}).json()["session_id"]
        normal_sid = client.post("/v1/sessions", json={}).json()["session_id"]
        client.patch(f"/v1/sessions/{archived_sid}", json={"archived": True})

        resp = client.delete("/v1/sessions/archived")

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1
        assert resp.json()["session_ids"] == [archived_sid]
        with pytest.raises(SessionNotFoundError, match="会话不存在"):
            runtime.persistent_session_manager.open(archived_sid)
        assert runtime.persistent_session_manager.open(normal_sid).session_id == normal_sid


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


# ===== POST /ag-ui/run channel boundary =====


class TestAgUiRunChannelBoundary:
    def test_text_run_uses_text_prompt_without_mutating_voice_session(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        sid = client.post("/v1/sessions", json={"channel": "voice"}).json()["session_id"]
        assert runtime.persistent_session_manager.open(sid).current_channel == "voice"

        resp = client.post(
            "/ag-ui/run",
            json={
                "thread_id": sid,
                "run_id": "run-text-1",
                "messages": [{"id": "u-1", "role": "user", "content": "这是啥情况"}],
                "tools": [],
                "context": [],
                "forwarded_props": {},
                "state": {},
            },
            headers={"accept": "text/event-stream"},
        )

        assert resp.status_code == 200
        assert "new answer" in resp.text
        loaded = runtime.persistent_session_manager.open(sid)
        assert loaded.current_channel == "voice"
        assert _RouteLLM.seen_messages
        system_prompt = _RouteLLM.seen_messages[-1][0]["content"]
        assert "正在通过电话和用户对话" not in system_prompt

    def test_text_run_error_persists_run_error_event(
        self, client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = client.post("/v1/sessions", json={}).json()["session_id"]

        def _fail_stream(
            self: _RouteLLM,
            messages: list[dict[str, Any]],
            *,
            tools: list[dict[str, Any]] | None = None,
            **overrides: Any,
        ) -> Iterator[LLMStreamEvent]:
            raise RuntimeError("upstream overloaded")
            yield  # pragma: no cover

        monkeypatch.setattr(_RouteLLM, "stream", _fail_stream)

        resp = client.post(
            "/ag-ui/run",
            json={
                "thread_id": sid,
                "run_id": "run-error-1",
                "messages": [{"id": "u-1", "role": "user", "content": "继续说"}],
                "tools": [],
                "context": [],
                "forwarded_props": {},
                "state": {},
            },
            headers={"accept": "text/event-stream"},
        )

        assert resp.status_code == 200
        assert "RUN_ERROR" in resp.text
        assert "这轮回答中断了，没有生成完整回复。请稍后重试。" in resp.text
        loaded = runtime.persistent_session_manager.open(sid)
        run_errors = [event for event in loaded.events if event.type == "run_error"]
        assert len(run_errors) == 1
        assert run_errors[0].payload == {
            "code": "internal_error",
            "message": "这轮回答中断了，没有生成完整回复。请稍后重试。",
            "recoverable": False,
            "source": "ag_ui",
            "run_id": "run-error-1",
        }


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
