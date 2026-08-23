from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    EffectivePersonaStore,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    NullSessionStore,
    PersonaCatalog,
    PersonaEditorWorkspace,
    PersonaPersistError,
    SessionManager,
    ToolRegistry,
)
from llm_providers import (
    LanguageModel,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ModelRuntimeConfig,
)


class FakeLLM:
    context_window = 128_000
    received_streams: ClassVar[list[list[dict[str, Any]]]] = []

    def __init__(self) -> None:
        self.stream_calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text=json.dumps(
                {"kind": "candidate", "content": "更简短的朋友", "summary": ["回复更短"]},
                ensure_ascii=False,
            )
        )

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        self.received_streams.append(request.messages)
        self.stream_calls += 1
        if request.tools and self.stream_calls == 1:
            yield LLMToolCallDelta(
                index=0,
                tool_call_id="proposal-1",
                tool_name="propose_persona",
                args_json_delta=json.dumps(
                    {"content": "更简短的朋友", "summary": ["回复更短"]},
                    ensure_ascii=False,
                ),
            )
            yield LLMTurnDone(stop_reason="tool_use")
            return
        if request.tools:
            yield LLMTextDelta(text="候选版本已经准备好了。")
            yield LLMTurnDone(stop_reason="end_turn")
            return
        assert not request.tools
        yield LLMTextDelta(text="试聊回复")
        yield LLMTurnDone(stop_reason="end_turn")


@pytest.fixture
def runtime(tmp_path: Path) -> BridgeRuntime:
    settings = BridgeSettings(
        sessions_dir=tmp_path / "sessions",
        personas_dir=tmp_path / "personas",
        memory_enabled=False,
        im_enabled=False,
    )
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    transient_store = NullSessionStore()
    catalog = PersonaCatalog(external_dir=settings.personas_dir)
    registry = ToolRegistry([])

    def llm_factory(model: ModelRuntimeConfig) -> LanguageModel:
        return FakeLLM()

    def prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    manager = SessionManager(
        store=persistent_store,
        llm_client_factory=llm_factory,
        prompt_builder_factory=prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=registry,
    )
    persona_store = EffectivePersonaStore(
        user_file=settings.personas_dir / "default" / "persona.md",
        internal_dir=settings.personas_dir / ".internal" / "default",
        builtin_loader=lambda: "默认朋友",
    )
    persona_editor_workspace = PersonaEditorWorkspace(
        persona_store,
        settings.personas_dir / ".internal" / "default" / "editor-base-revision.txt",
    )
    return BridgeRuntime(
        settings=settings,
        persistent_store=persistent_store,
        transient_store=transient_store,
        persistent_session_manager=manager,
        catalog=catalog,
        tool_registry=registry,
        prompt_builder_factory=prompt_factory,
        default_persona="default",
        default_model="fake/model",
        llm_client_factory=llm_factory,
        persona_store=persona_store,
        persona_editor_workspace=persona_editor_workspace,
    )


@pytest.fixture
def client(runtime: BridgeRuntime) -> TestClient:
    return TestClient(create_app_with_runtime(runtime))


def test_get_apply_and_conflict(client: TestClient, runtime: BridgeRuntime) -> None:
    initial = client.get("/v1/persona").json()
    assert initial["source"] == "user"
    assert initial["displayName"] == "你的朋友"

    applied = client.put(
        "/v1/persona",
        json={
            "content": "更简短的朋友",
            "expectedRevision": initial["revision"],
            "source": "guided",
        },
    )
    assert applied.status_code == 200
    assert applied.json()["content"] == "更简短的朋友"

    conflict = client.put(
        "/v1/persona",
        json={
            "content": "旧草稿",
            "expectedRevision": initial["revision"],
            "source": "guided",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "persona_revision_conflict"


def test_materialize_undo_and_reset(client: TestClient, runtime: BridgeRuntime) -> None:
    materialized = client.post("/v1/persona/materialize")
    assert materialized.status_code == 200
    assert runtime.persona_store is not None
    assert runtime.persona_store.user_file.exists()

    base = materialized.json()
    client.put(
        "/v1/persona",
        json={
            "content": "修改后",
            "expectedRevision": base["revision"],
            "source": "guided",
        },
    )
    undo = client.post("/v1/persona/undo")
    assert undo.status_code == 200
    assert undo.json()["content"] == "默认朋友"

    reset = client.delete("/v1/persona/customization")
    assert reset.status_code == 200
    assert reset.json()["source"] == "user"
    assert runtime.persona_store.user_file.exists()


def test_persona_editor_uses_dedicated_persistent_ag_ui_session(
    client: TestClient, runtime: BridgeRuntime
) -> None:
    snapshot = client.get("/v1/persona/editor")
    assert snapshot.status_code == 200
    thread_id = snapshot.json()["threadId"]
    assert snapshot.json()["events"] == []

    response = client.post(
        "/ag-ui/persona-editor/run",
        json={
            "threadId": thread_id,
            "runId": "run-1",
            "messages": [{"id": "user-1", "role": "user", "content": "说话短一点"}],
            "tools": [],
            "context": [],
            "state": {},
            "forwardedProps": {},
        },
        headers={"accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert "propose_persona" in response.text
    assert "候选版本已经准备好了" in response.text
    saved = client.get("/v1/persona/editor").json()
    assert saved["candidate"]["content"] == "更简短的朋友"
    assert saved["candidate"]["displayName"] == "你的朋友"
    assert any(event["type"] == "user_message" for event in saved["events"])
    assert runtime.persistent_store.list() == []

    current = client.get("/v1/persona").json()
    applied = client.put(
        "/v1/persona",
        json={
            "content": saved["candidate"]["content"],
            "expectedRevision": current["revision"],
            "source": "guided",
        },
    )
    assert applied.status_code == 200
    assert client.get("/v1/persona/editor").json()["events"] == []


def test_editor_candidate_keeps_session_base_revision_after_external_edit(
    client: TestClient, runtime: BridgeRuntime
) -> None:
    initial = client.get("/v1/persona").json()
    thread_id = client.get("/v1/persona/editor").json()["threadId"]

    def run_editor(run_id: str, text: str) -> None:
        response = client.post(
            "/ag-ui/persona-editor/run",
            json={
                "threadId": thread_id,
                "runId": run_id,
                "messages": [{"id": f"user-{run_id}", "role": "user", "content": text}],
                "tools": [],
                "context": [],
                "state": {},
                "forwardedProps": {},
            },
            headers={"accept": "text/event-stream"},
        )
        assert response.status_code == 200

    run_editor("run-1", "说话短一点")
    assert runtime.persona_store is not None
    runtime.persona_store.user_file.write_text("外部修改后的正式人格\n", encoding="utf-8")
    run_editor("run-2", "再自然一点")

    candidate = client.get("/v1/persona/editor").json()["candidate"]
    assert candidate["baseRevision"] == initial["revision"]
    conflict = client.put(
        "/v1/persona",
        json={
            "content": candidate["content"],
            "expectedRevision": candidate["baseRevision"],
            "source": "guided",
        },
    )
    assert conflict.status_code == 409


def test_discard_editor_reports_workspace_cleanup_failure(
    client: TestClient, runtime: BridgeRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = client.get("/v1/persona/editor").json()["threadId"]
    response = client.post(
        "/ag-ui/persona-editor/run",
        json={
            "threadId": thread_id,
            "runId": "run-before-discard",
            "messages": [{"id": "user-1", "role": "user", "content": "说话短一点"}],
            "tools": [],
            "context": [],
            "state": {},
            "forwardedProps": {},
        },
        headers={"accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert runtime.persona_editor_workspace is not None

    def fail_clear() -> None:
        raise PersonaPersistError("模拟清理失败")

    monkeypatch.setattr(runtime.persona_editor_workspace, "clear", fail_clear)
    discarded = client.delete("/v1/persona/editor")

    assert discarded.status_code == 500
    assert discarded.json()["error"]["code"] == "persona_editor_discard_failed"
