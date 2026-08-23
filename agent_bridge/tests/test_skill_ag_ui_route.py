"""AG-UI structured Skill selection integration coverage."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

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
    SkillCatalogService,
    make_default_registry,
)
from llm_providers import (
    LanguageModel,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ModelRuntimeConfig,
)


class _RecordingLLM:
    context_window = 128_000
    seen_messages: ClassVar[list[list[dict[str, Any]]]] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        self.seen_messages.append(request.messages)
        yield LLMTextDelta("ok")
        yield LLMTurnDone(stop_reason="end_turn")


def test_structured_skill_selection_reaches_ag_ui_model_context(tmp_path: Path) -> None:
    package = tmp_path / "skills" / "route-skill"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: route-skill\ndescription: Route integration test\n---\n\n"
        "AG UI ROUTE SKILL BODY\n",
        encoding="utf-8",
    )
    skill_service = SkillCatalogService(
        personal_root=tmp_path / "skills",
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = skill_service.initialize()
    selected = next(item for item in snapshot.entries if item.name == "route-skill")

    settings = BridgeSettings(sessions_dir=tmp_path / "sessions", memory_enabled=False)
    store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()
    _RecordingLLM.seen_messages = []

    def llm_factory(model: ModelRuntimeConfig) -> LanguageModel:
        return _RecordingLLM()

    def prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    manager = SessionManager(
        store=store,
        llm_client_factory=llm_factory,
        prompt_builder_factory=prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
        skill_service=skill_service,
    )
    runtime = BridgeRuntime(
        settings=settings,
        persistent_store=store,
        transient_store=store,
        persistent_session_manager=manager,
        catalog=catalog,
        tool_registry=tool_registry,
        prompt_builder_factory=prompt_factory,
        default_persona=catalog.find_by_name("default").name,
        default_model="deepseek/deepseek-chat",
        skill_service=skill_service,
    )
    client = TestClient(create_app_with_runtime(runtime))

    response = client.post(
        "/ag-ui/run",
        headers={"Accept": "text/event-stream"},
        json={
            "thread_id": "skill-thread",
            "run_id": "skill-run",
            "messages": [
                {
                    "id": "skill-user-message",
                    "role": "user",
                    "content": "use the selected workflow",
                }
            ],
            "tools": [],
            "context": [],
            "forwarded_props": {
                "skills": [{"id": selected.id, "expected_version": selected.version}]
            },
            "state": {},
        },
    )

    assert response.status_code == 200
    assert any(
        "AG UI ROUTE SKILL BODY" in item["content"] for item in _RecordingLLM.seen_messages[0]
    )
