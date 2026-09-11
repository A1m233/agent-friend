import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from agent_bridge.model_connections.runtime import ConnectionRuntime
from llm_providers.connections import model_reference
from llm_providers.errors import LLMModelUnavailableError

from llm_providers import LLMClient, default_model_runtime_config
from runtime_config import DesktopSettingsReader, EnvSource


def fixture(tmp_path: Path) -> tuple[ConnectionRuntime, dict[str, Any], str]:
    identity, revision, model = (str(UUID(int=i)) for i in (1, 2, 3))
    config = {
        "revision": 1,
        "connections": [
            {
                "id": identity,
                "name": "Custom",
                "baseUrl": "https://one.example/v1",
                "protocol": "responses",
                "credentialRevision": revision,
                "models": [{"id": model, "name": "same", "contextWindow": 8192}],
            }
        ],
    }
    reader = DesktopSettingsReader(tmp_path / "settings.json")
    reader.path.write_text(json.dumps({"modelConnections": config}), encoding="utf-8")
    source = EnvSource({f"CUSTOM_LLM_{identity}_{revision}_API_KEY": "fixture"})
    return ConnectionRuntime(source, reader), config, model_reference(identity, model)


def test_execution_keeps_old_endpoint_while_next_execution_reads_edit(tmp_path: Path) -> None:
    runtime, config, reference = fixture(tmp_path)
    selection = default_model_runtime_config(reference)
    with runtime.bind(selection) as old:
        assert isinstance(old, LLMClient)
        token = runtime.prepare([])
        config["connections"][0]["baseUrl"] = "https://two.example/v1"
        runtime.reader.path.write_text(json.dumps({"modelConnections": config}), encoding="utf-8")
        with pytest.raises(LLMModelUnavailableError), runtime.bind(selection):
            pytest.fail("Admission must be blocked while publication is pending")
        runtime.finish(token)
        with runtime.bind(selection) as new:
            assert isinstance(new, LLMClient)
            assert new.spec.api_base == "https://two.example/v1"
            assert old.spec.api_base == "https://one.example/v1"


def test_delete_waits_for_bound_execution_and_errors_release_lease(tmp_path: Path) -> None:
    runtime, config, reference = fixture(tmp_path)
    with pytest.raises(RuntimeError), runtime.bind(default_model_runtime_config(reference)):
        with pytest.raises(ValueError, match="connection_in_use"):
            runtime.prepare([config["connections"][0]["id"]])
        raise RuntimeError("cancel fixture")
    token = runtime.prepare([config["connections"][0]["id"]])
    runtime.reader.path.write_text(
        json.dumps({"modelConnections": {"revision": 2, "connections": []}}), encoding="utf-8"
    )
    runtime.finish(token)
    with (
        pytest.raises(LLMModelUnavailableError),
        runtime.bind(default_model_runtime_config(reference)),
    ):
        pytest.fail("Deleted reference must not fall back to a builtin provider")
    # Historical identity remains structurally readable after removal.
    assert default_model_runtime_config(reference).model_id == reference


def test_invalid_committed_configuration_keeps_admission_blocked(tmp_path: Path) -> None:
    runtime, config, reference = fixture(tmp_path)
    token = runtime.prepare([])
    runtime.reader.path.write_text('{"modelConnections": {"revision": "bad"}}', encoding="utf-8")
    with pytest.raises(ValueError):
        runtime.finish(token)
    with (
        pytest.raises(LLMModelUnavailableError),
        runtime.bind(default_model_runtime_config(reference)),
    ):
        pytest.fail("Invalid configuration cannot execute")
    runtime.reader.path.write_text(json.dumps({"modelConnections": config}), encoding="utf-8")
    runtime.finish(token)


def test_owner_routes_validate_defaults_and_recover_lost_prepare_response(tmp_path: Path) -> None:
    from typing import cast

    from agent_bridge.assembly import BridgeRuntime
    from agent_bridge.lifecycle import BridgeLifecycleController
    from agent_bridge.model_connections.routes import register_routes
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    runtime, _config, reference = fixture(tmp_path)
    app = FastAPI()
    register_routes(
        app, BridgeLifecycleController(cast(BridgeRuntime, None), token="owner"), runtime
    )
    client = TestClient(app)
    url = "/internal/model-connections/prepare"
    body = {
        "operationId": "0123456789abcdef",
        "modelConnections": {"revision": 2, "connections": []},
        "chatModel": {"provider": "custom", "model": reference},
        "memoryModel": {"provider": "deepseek", "model": "deepseek/deepseek-flash"},
    }
    assert client.post(url, json=body).status_code == 401
    headers = {"x-agent-friend-lifecycle-token": "owner"}
    assert client.post(url, json=body, headers=headers).status_code == 400
    body["chatModel"] = body["memoryModel"]
    with runtime.bind(default_model_runtime_config(reference)):
        response = client.post(url, json=body, headers=headers)
        assert response.json()["detail"]["code"] == "connection_in_use"
    first = client.post(url, json=body, headers=headers)
    assert first.status_code == 200
    assert client.post(url, json=body, headers=headers).json() == first.json()
    # No publication simulates failed keyring/settings write. Finish resumes the old config.
    finish = "/internal/model-connections/finish"
    assert client.post(finish, json=first.json(), headers=headers).status_code == 200
    assert client.post(finish, json=first.json(), headers=headers).status_code == 200
    with runtime.bind(default_model_runtime_config(reference)):
        pass
    # A new bridge after restart resolves the same committed identity.
    restarted = ConnectionRuntime(runtime.source, runtime.reader)
    assert reference in restarted.directory().models


def test_removed_custom_reference_survives_session_storage(tmp_path: Path) -> None:
    from agent import JsonlSessionStore, Session

    runtime, _config, reference = fixture(tmp_path)
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="fixture", persona="default", model=reference)
    store.create(session)
    runtime.reader.path.write_text(
        json.dumps({"modelConnections": {"revision": 2, "connections": []}}), encoding="utf-8"
    )
    loaded = store.load(session.session_id)
    assert loaded.current_model == reference


def test_draft_discovery_uses_unsaved_address_key_and_never_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from agent_bridge.model_connections.inspection import inspect_draft

    runtime, _config, _reference = fixture(tmp_path)
    before = runtime.reader.path.read_bytes()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "private", "context_length": 12345}]})

    class Client(httpx.Client):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    result = inspect_draft(
        {
            "mode": "discover",
            "baseUrl": "https://new.example/v1",
            "protocol": "responses",
            "apiKey": "draft-secret",
        },
        runtime,
    )
    assert str(requests[0].url) == "https://new.example/v1/models"
    assert requests[0].headers["authorization"] == "Bearer draft-secret"
    assert result["models"][0]["contextWindow"] == 12345
    assert "draft-secret" not in json.dumps(result)
    assert runtime.reader.path.read_bytes() == before


def test_draft_probe_without_context_runs_bounded_generation_and_tool_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from agent_bridge.model_connections import inspection

    from llm_providers import LLMTurnDone, ModelRequest, ProviderAssistantTurn, ProviderToolCall

    runtime, _config, _reference = fixture(tmp_path)
    before = runtime.reader.path.read_bytes()
    calls: list[ModelRequest] = []

    class Client:
        def __init__(self, spec: Any, **kwargs: Any) -> None:
            assert spec.responses.model == "unknown-model"
            assert spec.api_key == "draft-secret"
            assert kwargs == {"network_retries": 0, "engine_overload_retries": 0}

        def generate(self, request: ModelRequest) -> Any:
            calls.append(request)
            return SimpleNamespace(text="OK" if len(calls) == 1 else "connection-ok-121")

        def stream(self, request: ModelRequest) -> Any:
            calls.append(request)
            yield LLMTurnDone(
                "tool_use",
                assistant_turn=ProviderAssistantTurn(
                    None, (ProviderToolCall(0, "call-1", "connection_check", "{}"),)
                ),
            )

    monkeypatch.setattr(inspection, "LLMClient", Client)
    result = inspection.inspect_draft(
        {
            "mode": "probe",
            "baseUrl": "https://new.example/v1",
            "protocol": "responses",
            "apiKey": "draft-secret",
            "model": "unknown-model",
        },
        runtime,
    )
    assert result["generation"]["status"] == result["tools"]["status"] == "passed"
    assert result["metadata"]["contextWindow"] is None
    assert len(calls) == 3 and all(not call.control.allow_retries for call in calls)
    assert calls[-1].messages[-1]["tool_call_id"] == "call-1"
    assert runtime.reader.path.read_bytes() == before


def test_probe_auth_failure_is_redacted_and_skips_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_bridge.model_connections import inspection
    from llm_providers.errors import LLMAuthError

    runtime, _config, _reference = fixture(tmp_path)
    calls = []

    class Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def generate(self, request: Any) -> Any:
            calls.append(request)
            raise LLMAuthError("draft-secret in upstream message")

        def stream(self, request: Any) -> Any:
            pytest.fail("must not probe tools after auth failure")

    monkeypatch.setattr(inspection, "LLMClient", Client)
    result = inspection.inspect_draft(
        {
            "mode": "probe",
            "baseUrl": "https://new.example/v1",
            "protocol": "responses",
            "apiKey": "draft-secret",
            "model": "unknown-model",
        },
        runtime,
    )
    assert result["generation"] == {"status": "failed", "code": "authentication_failed"}
    assert result["tools"]["status"] == "skipped"
    assert len(calls) == 1 and "draft-secret" not in json.dumps(result)


def test_connection_edit_can_preserve_but_not_select_removed_default(tmp_path: Path) -> None:
    from typing import cast

    from agent_bridge.assembly import BridgeRuntime
    from agent_bridge.lifecycle import BridgeLifecycleController
    from agent_bridge.model_connections.routes import register_routes
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    runtime, config, _ref = fixture(tmp_path)
    old = {"provider": "deepseek", "model": "deepseek/deepseek-v4-flash"}
    runtime.reader.path.write_text(
        json.dumps(
            {
                "modelConnections": config,
                "chatModel": old,
                "memoryModel": old,
            }
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    register_routes(
        app, BridgeLifecycleController(cast(BridgeRuntime, None), token="owner"), runtime
    )
    client = TestClient(app)
    headers = {"x-agent-friend-lifecycle-token": "owner"}
    body = {
        "operationId": "0123456789abcdef",
        "modelConnections": config,
        "chatModel": old,
        "memoryModel": old,
    }
    before = runtime.reader.path.read_bytes()
    result = client.post("/internal/model-connections/prepare", json=body, headers=headers)
    assert result.status_code == 200
    runtime.finish(result.json()["operationId"])
    assert runtime.reader.path.read_bytes() == before
    body["memoryModel"] = {"provider": "deepseek", "model": "deepseek/deepseek-v4-flash-vision-exp"}
    assert (
        client.post("/internal/model-connections/prepare", json=body, headers=headers).status_code
        == 400
    )
