"""026 · /v1/memory/* 路由集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.dev.recall_buffer import RecallBuffer
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient
from memory.facade import Memory

from agent import (
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    make_default_registry,
)
from memory import EpisodicRow, SemanticRow, SqliteMemoryStore

if TYPE_CHECKING:
    from llm_providers import LLMClient


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

    def _llm_factory(model: str) -> LLMClient:
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

    def test_search_semantic(self, client: TestClient) -> None:
        resp = client.get("/v1/memory/search?q=Tom&layer=semantic")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["semantic"]) >= 1
        assert any("Tom" in r["row"]["statement"] for r in data["semantic"])


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
