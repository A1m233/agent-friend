"""BridgeRuntime assembly regression tests."""

from __future__ import annotations

from typing import Any, cast

import pytest
from agent.tools.builtin.conversation_history import ConversationHistoryTool
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import build_runtime
from agent_bridge.errors import map_exception
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient
from llm_providers.spec import DEFAULT_LLM_TIMEOUT_SECONDS

from runtime_config import CredentialMissingError, credential_source_for_mode


def test_persistent_runtime_registers_recall_past_chats(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent sessions must expose the conversation-history tool.

    IM uses the persistent SessionBridge path, so losing this registration makes
    real IM sessions record "未注册的工具: 'recall_past_chats'".
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    settings = BridgeSettings(
        sessions_dir=tmp_path / "sessions",
        personas_dir=tmp_path / "personas",
        memory_enabled=False,
        im_enabled=False,
    )
    runtime = build_runtime(settings)

    try:
        tool_names = {tool.name for tool in runtime.tool_registry.all_tools()}
        manager_registry = cast(
            Any,
            runtime.persistent_session_manager,
        )._tool_registry

        assert "recall_past_chats" in tool_names
        assert manager_registry is runtime.tool_registry
        assert (settings.personas_dir / "default" / "persona.md").is_file()
    finally:
        runtime.close()


def test_memory_enabled_registers_history_and_structured_sources(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    runtime = build_runtime(
        BridgeSettings(
            sessions_dir=tmp_path / "sessions",
            personas_dir=tmp_path / "personas",
            memory_db=tmp_path / "memory.db",
            memory_enabled=True,
            im_enabled=False,
        )
    )

    try:
        history_tool = next(
            tool
            for tool in runtime.tool_registry.all_tools()
            if isinstance(tool, ConversationHistoryTool)
        )
        assert history_tool._search_service.source_names == (
            "session_history",
            "structured_memory",
        )
    finally:
        runtime.close()


def test_runtime_starts_without_llm_credentials_for_local_control_plane(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    runtime = build_runtime(
        BridgeSettings(
            sessions_dir=tmp_path / "sessions",
            personas_dir=tmp_path / "personas",
            memory_db=tmp_path / "memory.db",
            memory_enabled=True,
            im_enabled=False,
        ),
        runtime_config=resolve_bridge_runtime_config(credential_source_for_mode("env-only")),
    )

    try:
        assert runtime.memory is not None
        assert runtime.persona_store is not None
        assert runtime.persona_store.user_file.is_file()
        client = TestClient(create_app_with_runtime(runtime, lifecycle_token="owner-token"))
        inventory = client.get(
            "/internal/migration/inventory",
            headers={"X-Agent-Friend-Lifecycle-Token": "owner-token"},
        )
        assert inventory.status_code == 200

        initial = client.get("/v1/persona").json()
        customized = client.put(
            "/v1/persona",
            json={
                "content": "临时自定义人格",
                "expectedRevision": initial["revision"],
                "source": "guided",
            },
        )
        assert customized.status_code == 200
        reset = client.delete("/v1/persona/customization")
        assert reset.status_code == 200

        with pytest.raises(CredentialMissingError):
            runtime.llm_client_factory(runtime.default_model)  # type: ignore[misc]
    finally:
        runtime.close()


def test_missing_credentials_map_to_recoverable_protocol_error() -> None:
    error = map_exception(CredentialMissingError(["DEEPSEEK_API_KEY"]))

    assert error.http_status == 503
    assert error.code == "credential_missing"
    assert error.recoverable is True


def test_memory_spec_defaults_to_v4_pro_with_thinking_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.delenv("DEEPSEEK_MEMORY_MODEL", raising=False)

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    spec = runtime_config.memory_spec

    assert spec.model == "deepseek/deepseek-v4-pro"
    assert spec.defaults["extra_body"] == {"thinking": {"type": "disabled"}}
    assert spec.defaults["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS


def test_memory_spec_allows_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.setenv("DEEPSEEK_MEMORY_MODEL", "deepseek/custom-memory")

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    spec = runtime_config.memory_spec

    assert spec.model == "deepseek/custom-memory"


def test_runtime_config_allows_provider_timeout_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "12.5")

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    client = runtime_config.llm_client_factory(runtime_config.default_model)

    assert runtime_config.memory_spec.defaults["timeout"] == 12.5
    assert client.spec.defaults["timeout"] == 12.5


def test_runtime_config_builds_kimi_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "test-kimi-key")
    monkeypatch.setenv("LLM_CHAT_PROVIDER", "kimi")
    monkeypatch.setenv("LLM_CHAT_MODEL", "kimi/kimi-k2.7-code-highspeed")
    monkeypatch.setenv("LLM_MEMORY_PROVIDER", "kimi")
    monkeypatch.setenv("LLM_MEMORY_MODEL", "kimi/kimi-k2.6")

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    client = runtime_config.llm_client_factory(runtime_config.default_model)

    assert runtime_config.default_model == "kimi/kimi-k2.7-code-highspeed"
    assert runtime_config.memory_spec.model == "openai/kimi-k2.6"
    assert runtime_config.memory_spec.api_base == "https://api.moonshot.cn/v1"
    assert runtime_config.memory_spec.defaults["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS
    assert client.spec.model == "openai/kimi-k2.7-code-highspeed"
    assert client.spec.api_base == "https://api.moonshot.cn/v1"
    assert client.spec.defaults["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS
