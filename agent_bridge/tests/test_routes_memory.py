"""026 · /v1/memory/* 路由集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.dev.recall_buffer import RecallBuffer
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient
from memory.contracts import RecallTrace, RecallTraceItem
from memory.core_profile import normalize_core_value
from memory.facade import Memory
from memory.host_episodes import presentation

from agent import (
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    make_default_registry,
)
from memory import CoreProfileEntry, EpisodicRow, SemanticRow, SqliteMemoryStore

if TYPE_CHECKING:
    from llm_providers import LLMClient, ModelRuntimeConfig


def _memory_with_data() -> Memory:
    """构造一个带少量数据的 Memory 实例（无真实 LLM，只走 store 读路径）。"""
    store = SqliteMemoryStore(":memory:")
    now = datetime.now(UTC)
    store.add_semantic(
        SemanticRow(
            id="s1",
            statement="用户养了一只叫 Tom 的猫",
            persona_id="p1",
            created_at=now,
            updated_at=now,
            pinned=True,
        )
    )
    store.add_semantic(
        SemanticRow(
            id="s2",
            statement="用户讨厌香菜",
            persona_id="p1",
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
        )
    )
    store.add_episodic(
        EpisodicRow(
            id="e1",
            summary="用户分享了新养的猫 Tom",
            source_ref="s1#a..b",
            persona_id="p1",
            occurred_at=now,
            created_at=now,
        )
    )
    with store.extraction_batch(
        observation_id="obs-1",
        session_id="s1",
        first_source_ref="s1#a",
        last_source_ref="s1#b",
        output_sha256="test",
        output_contract_version=2,
        committed_at=now,
    ) as tx:
        assert tx is not None
        tx.add_semantic_change(
            change_id="change-1",
            observation_id="obs-1",
            owner_user_id="local",
            persona_id="p1",
            origin="extracted",
            provenance=["s1#a", "s1#b"],
            created_at=now,
        )
        tx.add_semantic_change_member(
            change_id="change-1",
            semantic_id="s1",
            role="introduced",
            reason="测试中的普通长期事实",
        )
    store.add_core_profile_entry(
        CoreProfileEntry(
            id="core-name",
            owner_user_id="local",
            scope_kind="user",
            persona_id="",
            field_key="user.preferred_name",
            value="小陈",
            normalized_value=normalize_core_value("小陈"),
            valid_from=now,
            valid_until=None,
            supersedes_id=None,
            provenance=(),
            created_at=now,
            updated_at=now,
        )
    )
    store.add_episodic(
        EpisodicRow(
            id="e2",
            summary="p2 的事",
            source_ref="s1#c..d",
            persona_id="p2",
            occurred_at=now,
            created_at=now,
        )
    )

    # Memory 需要 extractor / reconciler，但 retrieve 不触发它们；给最小占位。
    from memory import Extractor, KeywordRetrieval, Reconciler

    class FakeLLM:
        def complete(self, messages: list[dict[str, object]], **_kw: object) -> str:
            return ""

    memory = Memory(
        store,
        Extractor(FakeLLM(), prompt="x"),  # type: ignore[arg-type]
        Reconciler(store),
        retrieval=KeywordRetrieval(store),
        pinned_relevance_gate=False,
    )
    return memory


@pytest.fixture
def runtime(tmp_path: Path) -> BridgeRuntime:
    """构造一个启用 memory 的 BridgeRuntime。"""
    settings = BridgeSettings(sessions_dir=tmp_path / "sessions")
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()

    def _llm_factory(model: ModelRuntimeConfig) -> LLMClient:
        from llm_providers import LLMClient, ProviderSpec

        return LLMClient(ProviderSpec(model=model.model_id, api_key="sk-test"))

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    persistent_session_manager = SessionManager(
        store=persistent_store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
    )

    recall_buffer = RecallBuffer()
    memory = _memory_with_data()
    memory._on_retrieved = recall_buffer.record

    return BridgeRuntime(
        settings=settings,
        persistent_store=persistent_store,
        transient_store=persistent_store,
        persistent_session_manager=persistent_session_manager,
        catalog=catalog,
        tool_registry=tool_registry,
        prompt_builder_factory=_prompt_factory,
        default_persona=catalog.find_by_name("default").name,
        default_model="deepseek/deepseek-chat",
        memory=memory,
        recall_buffer=recall_buffer,
    )


@pytest.fixture
def client(runtime: BridgeRuntime) -> TestClient:
    app = create_app_with_runtime(runtime)
    return TestClient(app)


class TestMemoryDisabled:
    def test_owner_mode_disabled_does_not_register_inspector_routes(
        self, runtime: BridgeRuntime
    ) -> None:
        app = create_app_with_runtime(runtime, memory_inspector_enabled=False)
        test_client = TestClient(app)
        assert test_client.get("/v1/memory/semantic").status_code == 404
        assert test_client.get("/v1/memory/semantic-changes").status_code == 404

    def test_memory_disabled_returns_503(self, tmp_path: Path) -> None:
        settings = BridgeSettings(sessions_dir=tmp_path / "sessions")
        persistent_store = JsonlSessionStore(settings.sessions_dir)
        catalog = PersonaCatalog()
        tool_registry = make_default_registry()
        runtime = BridgeRuntime(
            settings=settings,
            persistent_store=persistent_store,
            transient_store=persistent_store,
            persistent_session_manager=SessionManager(
                store=persistent_store,
                llm_client_factory=lambda m: None,  # type: ignore[arg-type,return-value]
                prompt_builder_factory=lambda p: MarkdownPromptBuilder(persona_id=p),
                context_manager_factory=NaiveContextManager,
                tool_registry=tool_registry,
            ),
            catalog=catalog,
            tool_registry=tool_registry,
            prompt_builder_factory=lambda p: MarkdownPromptBuilder(persona_id=p),
            default_persona=catalog.find_by_name("default").name,
            default_model="deepseek/deepseek-chat",
        )
        app = create_app_with_runtime(runtime)
        test_client = TestClient(app)
        assert test_client.get("/v1/memory/semantic").status_code == 503
        assert test_client.get("/v1/memory/episodic").status_code == 503
        assert test_client.get("/v1/memory/recalls").status_code == 503
        assert (
            test_client.post(
                "/v1/memory/recall-probe",
                json={"query": "x", "persona_id": "p1"},
            ).status_code
            == 503
        )


class TestSemanticRoutes:
    def test_list_semantic(self, client: TestClient) -> None:
        resp = client.get("/v1/memory/semantic")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "s1"
        assert data[0]["pinned"] is True

    def test_core_filter_and_projection(self, client: TestClient) -> None:
        filtered = client.get("/v1/memory/semantic?kind=core&persona_id=p1")
        assert filtered.status_code == 200
        assert filtered.json()[0]["field_key"] == "user.preferred_name"
        assert filtered.json()[0]["kind"] == "core"

        profile = client.get("/v1/memory/core-profile?persona_id=p1")
        assert profile.status_code == 200
        assert profile.json()["profile"]["user"]["preferred_name"] == "小陈"

    def test_search_semantic(self, client: TestClient) -> None:
        resp = client.get("/v1/memory/search?q=Tom&layer=semantic")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["semantic"]) >= 1
        assert any("Tom" in r["row"]["statement"] for r in data["semantic"])

    def test_list_semantic_changes_with_contract_version(self, client: TestClient) -> None:
        resp = client.get("/v1/memory/semantic-changes?persona_id=p1")
        assert resp.status_code == 200
        assert resp.json() == [
            {
                "id": "change-1",
                "observation_id": "obs-1",
                "owner_user_id": "local",
                "persona_id": "p1",
                "origin": "extracted",
                "provenance": ["s1#a", "s1#b"],
                "created_at": resp.json()[0]["created_at"],
                "output_contract_version": 2,
                "members": [
                    {
                        "semantic_id": "s1",
                        "role": "introduced",
                        "reason": "测试中的普通长期事实",
                        "statement": "用户养了一只叫 Tom 的猫",
                        "valid_from": None,
                        "valid_until": None,
                    }
                ],
            }
        ]


class TestEpisodicRoutes:
    def test_list_episodic_filtered_by_persona(self, client: TestClient) -> None:
        resp = client.get("/v1/memory/episodic?persona_id=p1")
        assert resp.status_code == 200
        data = resp.json()
        assert [r["id"] for r in data] == ["e1"]

    def test_list_episodic_all_personas(self, client: TestClient) -> None:
        resp = client.get("/v1/memory/episodic")
        assert resp.status_code == 200
        data = resp.json()
        assert {r["id"] for r in data} == {"e1", "e2"}

    @pytest.mark.parametrize("route", ["episodic", "search"])
    def test_notification_summary_projects_local_time_without_changing_evidence(
        self,
        client: TestClient,
        runtime: BridgeRuntime,
        monkeypatch: pytest.MonkeyPatch,
        route: str,
    ) -> None:
        occurred_at = datetime(2026, 9, 6, 9, 9, 26, 5000, tzinfo=UTC)
        local_zone = timezone(timedelta(hours=8))
        monkeypatch.setattr(
            presentation,
            "format_notification_time",
            lambda value: value.astimezone(local_zone).strftime("%Y-%m-%d %H:%M:%S") + " +08:00",
        )
        original_prefix = "2026-09-06T09:09:26.005000+00:00"
        quoted_suffix = (
            "，我通过桌宠气泡就「clockproof」发出提醒，实际显示："
            '"17:10 提醒喝水，原文还包含 2026-09-06T09:10:00+00:00。"。'
        )
        original_summary = original_prefix + quoted_suffix
        notification = EpisodicRow(
            id="notification-presented:clockproof",
            summary=original_summary,
            source_ref="notification:clockproof",
            persona_id="p1",
            occurred_at=occurred_at,
            created_at=occurred_at,
        )
        ordinary = EpisodicRow(
            id="ordinary-clockproof",
            summary=original_summary,
            source_ref="session-clockproof#event",
            persona_id="p1",
            occurred_at=occurred_at,
            created_at=occurred_at,
        )
        assert runtime.memory is not None
        runtime.memory.store.add_episodic(notification)
        runtime.memory.store.add_episodic(ordinary)

        if route == "search":
            response = client.get(
                "/v1/memory/search",
                params={"q": "clockproof", "layer": "episodic", "persona_id": "p1"},
            )
            assert response.status_code == 200
            rows = [match["row"] for match in response.json()["episodic"]]
        else:
            response = client.get("/v1/memory/episodic", params={"persona_id": "p1"})
            assert response.status_code == 200
            rows = response.json()
        by_id = {row["id"]: row for row in rows}
        shown = by_id[notification.id]
        assert shown["summary"] == "2026-09-06 17:09:26 +08:00" + quoted_suffix
        assert shown["occurred_at"] == "2026-09-06T09:09:26.005000Z"
        assert shown["created_at"] == "2026-09-06T09:09:26.005000Z"
        assert shown["source_ref"] == notification.source_ref
        assert by_id[ordinary.id]["summary"] == original_summary
        stored = {
            row.id: row
            for row in runtime.memory.store.list_episodic(owner_user_id="local", persona_id="p1")
        }
        assert stored[notification.id] == notification
        assert stored[ordinary.id] == ordinary


class TestRecallProbe:
    def test_recall_probe_records_trace(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/memory/recall-probe",
            json={"query": "Tom", "persona_id": "p1", "top_k": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace"]["source"] == "probe"
        assert data["trace"]["query"] == "Tom"
        assert data["trace"]["top_k"] == 5

        # trace 进入 recalls
        resp2 = client.get("/v1/memory/recalls")
        assert resp2.status_code == 200
        assert resp2.json()[0]["source"] == "probe"

    def test_historical_notification_recall_trace_keeps_original_text(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        occurred_at = datetime(2026, 9, 6, 9, 9, 26, 5000, tzinfo=UTC)
        original_summary = (
            "2026-09-06T09:09:26.005000+00:00，我通过桌宠气泡就「clockproof」发出提醒，"
            '实际显示："17:10 提醒喝水。"。'
        )
        trace = RecallTrace(
            timestamp=occurred_at,
            query="clockproof",
            owner_user_id="local",
            persona_id="p1",
            top_k=8,
            source="natural",
            pinned_pre_gate=0,
            pinned_post_gate=0,
            gate_enabled=False,
            gate_mode=None,
            gate_decision="disabled",
            candidates_count=1,
            ranked_count=1,
            items=[
                RecallTraceItem(
                    text=original_summary,
                    layer="episodic",
                    source_ref="episodic:notification-presented:clockproof",
                    score=1.0,
                )
            ],
        )
        assert runtime.recall_buffer is not None
        runtime.recall_buffer.record(trace)

        response = client.get("/v1/memory/recalls")

        assert response.status_code == 200
        assert response.json()[0]["timestamp"] == "2026-09-06T09:09:26.005000Z"
        assert response.json()[0]["items"][0]["text"] == original_summary
        assert runtime.recall_buffer.snapshot()[0] == trace
