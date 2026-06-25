"""BridgeRuntime assembly regression tests."""

from __future__ import annotations

from typing import Any, cast

import pytest
from agent_bridge.assembly import _make_memory_spec_with_thinking_off, build_runtime
from agent_bridge.settings import BridgeSettings


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

    spec = _make_memory_spec_with_thinking_off()

    assert spec.model == "deepseek/deepseek-v4-pro"
    assert spec.defaults["extra_body"] == {"thinking": {"type": "disabled"}}


def test_memory_spec_allows_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.setenv("DEEPSEEK_MEMORY_MODEL", "deepseek/custom-memory")

    spec = _make_memory_spec_with_thinking_off()

    assert spec.model == "deepseek/custom-memory"
