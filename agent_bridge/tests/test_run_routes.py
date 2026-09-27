from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from agent.conversation_events import TextDelta
from agent.runtime import (
    AgentRuntime,
    ConsumerAbandonPolicy,
    InteractiveBusyPolicy,
    UserEvent,
)
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.runs import DESKTOP_TEXT_CONTROL_SCOPE
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    make_default_registry,
)

if TYPE_CHECKING:
    from llm_providers import LLMClient, ModelRuntimeConfig


def _runtime(tmp_path: Path) -> BridgeRuntime:
    settings = BridgeSettings(sessions_dir=tmp_path / "sessions", memory_enabled=False)
    store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tools = make_default_registry()

    def llm_factory(model: ModelRuntimeConfig) -> LLMClient:
        from llm_providers import LLMClient, ProviderSpec

        return LLMClient(ProviderSpec(model=model.model_id, api_key="sk-test"))

    def prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    manager = SessionManager(
        store=store,
        llm_client_factory=llm_factory,
        prompt_builder_factory=prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tools,
    )
    agent_runtime = AgentRuntime(conversation_factory=lambda _sid: cast(Conversation, None))
    return BridgeRuntime(
        settings=settings,
        persistent_store=store,
        transient_store=store,
        persistent_session_manager=manager,
        catalog=catalog,
        tool_registry=tools,
        prompt_builder_factory=prompt_factory,
        default_persona=catalog.find_by_name("default").name,
        default_model="deepseek/deepseek-chat",
        agent_runtime=agent_runtime,
    )


def test_run_query_returns_recent_terminal(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    assert runtime.agent_runtime is not None
    runtime.agent_runtime.submit(UserEvent(session_id="s", user_input="hello"), run_id="r1")
    client = TestClient(create_app_with_runtime(runtime))

    listed = client.get("/v1/runs")
    fetched = client.get("/v1/runs/r1")

    assert listed.status_code == 200
    assert [item["run_id"] for item in listed.json()["runs"]] == ["r1"]
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "terminal"
    assert fetched.json()["outcome"] == "rejected"


def test_cancel_rejects_a_different_runtime_instance(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    client = TestClient(create_app_with_runtime(runtime))
    response = client.post(
        "/v1/sessions/s/runs/r1/cancel?expectedRuntimeInstanceId=previous-bridge"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "run_runtime_mismatch"


def test_agui_attachment_validation_is_registered_before_preparation_failure(
    tmp_path: Path,
) -> None:
    from agent.runtime.hooks import PRE_TURN_PROCEED, HookKind, PreTurnDecision

    runtime = _runtime(tmp_path)
    agent_runtime = runtime.agent_runtime
    assert agent_runtime is not None
    hooks: list[str] = []

    def pre_turn(_event: object) -> PreTurnDecision:
        hooks.append("called")
        return PRE_TURN_PROCEED

    agent_runtime.register_hook(HookKind.PRE_TURN, pre_turn)
    client = TestClient(create_app_with_runtime(runtime))
    agent_runtime.start()
    try:
        response = client.post(
            "/ag-ui/run",
            headers={"Accept": "text/event-stream"},
            json={
                "thread_id": "preparing-session",
                "run_id": "preparing-run",
                "messages": [
                    {
                        "id": "draft-user",
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "url",
                                    "value": "/v1/attachments/drafts/abcdefghijklmnop/content",
                                    "mime_type": "image/png",
                                },
                            }
                        ],
                    }
                ],
                "tools": [],
                "context": [],
                "forwarded_props": {},
                "state": {},
            },
        )
        assert response.status_code == 200
        assert '"type":"RUN_STARTED"' in response.text
        assert '"type":"RUN_ERROR"' in response.text
        assert '"code":"attachment_store_unavailable"' in response.text
        assert '"inputAccepted":false' in response.text
        snapshot = agent_runtime.get_run_snapshot("preparing-run")
        assert snapshot is not None and snapshot.input_accepted is False
        assert snapshot.outcome == "failed"
        assert hooks == []
        assert not runtime.persistent_store.exists("preparing-session")
    finally:
        agent_runtime.stop()


def test_run_event_route_returns_runtime_owned_journal(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    assert runtime.agent_runtime is not None
    runtime.agent_runtime.submit(UserEvent(session_id="s", user_input="hello"), run_id="r1")
    runtime.agent_runtime.listeners.fan_out_event(
        UserEvent(session_id="s", user_input="hello"),
        TextDelta(text="continued"),
        run_id="r1",
    )
    client = TestClient(create_app_with_runtime(runtime))

    response = client.get("/v1/runs/r1/events?afterSeq=0")

    assert response.status_code == 200
    payload = response.json()
    assert runtime.agent_runtime is not None
    assert (
        payload["runtime_instance_id"] == runtime.agent_runtime.runtime_identity.bridge_instance_id
    )
    assert payload["events"] == [
        {"event_seq": 1, "event": {"type": "text_delta", "text": "continued"}}
    ]


def test_session_runtime_snapshot_is_content_free(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    client = TestClient(create_app_with_runtime(runtime))

    response = client.get("/v1/sessions/s/runtime-snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert runtime.agent_runtime is not None
    assert (
        payload["runtime"]["bridgeInstanceId"]
        == runtime.agent_runtime.runtime_identity.bridge_instance_id
    )
    assert payload["sessionRuntime"] is None
    assert payload["runs"] == []
    assert payload["approvals"] == []
    assert payload["commandProcesses"] == []


def test_precise_cancel_endpoint_only_controls_matching_desktop_run(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    agent_runtime = runtime.agent_runtime
    assert agent_runtime is not None
    agent_runtime.start()
    try:
        with agent_runtime.try_reserve_session_mutation("s"):
            handle = agent_runtime.submit(
                UserEvent(session_id="s", user_input="hello"),
                run_id="r1",
                channel="text",
                metadata={"protocol": "ag_ui"},
                consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
                interactive_busy_policy=InteractiveBusyPolicy.REJECT_IF_INTERACTIVE_BUSY,
                control_scope=DESKTOP_TEXT_CONTROL_SCOPE,
            )
            client = TestClient(create_app_with_runtime(runtime))
            mismatch = client.post("/v1/sessions/other/runs/r1/cancel")
            cancelled = client.post("/v1/sessions/s/runs/r1/cancel")

            assert mismatch.status_code == 409
            assert mismatch.json()["detail"]["error"] == "run_session_mismatch"
            assert cancelled.status_code == 200
            assert cancelled.json()["result"] == "accepted"
            assert cancelled.json()["run"]["outcome"] == "cancelled"
            assert handle.terminal is not None
    finally:
        agent_runtime.stop()


def test_precise_cancel_rejects_external_control_scope(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    agent_runtime = runtime.agent_runtime
    assert agent_runtime is not None
    agent_runtime.start()
    try:
        with agent_runtime.try_reserve_session_mutation("s"):
            agent_runtime.submit(
                UserEvent(session_id="s", user_input="hello"),
                run_id="voice-run",
                channel="voice",
                metadata={"protocol": "openai"},
                control_scope="voice",
            )
            client = TestClient(create_app_with_runtime(runtime))
            response = client.post("/v1/sessions/s/runs/voice-run/cancel")
            assert response.status_code == 409
            assert response.json()["detail"]["error"] == "run_not_desktop_controllable"
    finally:
        agent_runtime.stop()
