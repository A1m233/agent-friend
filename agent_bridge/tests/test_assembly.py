"""BridgeRuntime assembly regression tests."""

from __future__ import annotations

from typing import Any, cast

import pytest
from agent_bridge.assembly import build_runtime
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from agent_bridge.settings import BridgeSettings

from runtime_config import credential_source_for_mode


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

    runtime = build_runtime(
        BridgeSettings(
            sessions_dir=tmp_path / "sessions",
            personas_dir=tmp_path / "personas",
            memory_enabled=False,
            im_enabled=False,
        )
    )

    try:
        tool_names = {tool.name for tool in runtime.tool_registry.all_tools()}
        manager_registry = cast(
            Any,
            runtime.persistent_session_manager,
        )._tool_registry

        assert "recall_past_chats" in tool_names
        assert manager_registry is runtime.tool_registry
    finally:
        runtime.close()


def test_memory_spec_defaults_to_v4_pro_with_thinking_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.delenv("DEEPSEEK_MEMORY_MODEL", raising=False)

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    spec = runtime_config.memory_spec

    assert spec.model == "deepseek/deepseek-v4-pro"
    assert spec.defaults["extra_body"] == {"thinking": {"type": "disabled"}}


def test_memory_spec_allows_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.setenv("DEEPSEEK_MEMORY_MODEL", "deepseek/custom-memory")

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    spec = runtime_config.memory_spec

    assert spec.model == "deepseek/custom-memory"


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
    assert client.spec.model == "openai/kimi-k2.7-code-highspeed"
    assert client.spec.api_base == "https://api.moonshot.cn/v1"
