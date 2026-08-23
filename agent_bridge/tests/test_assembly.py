"""BridgeRuntime assembly regression tests."""

from __future__ import annotations

from typing import Any, cast

import agent_bridge.runtime_config as bridge_runtime_config_module
import pytest
from agent.tools.builtin.conversation_history import ConversationHistoryTool
from agent.tools.builtin.nearby_place_search import NearbyPlaceSearchTool
from agent.tools.builtin.nearby_place_search.providers import AmapNearbyPlaceProvider
from agent.tools.builtin.weather_query import WeatherQueryTool
from agent.tools.builtin.weather_query.providers import QWeatherProvider
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import build_runtime
from agent_bridge.errors import map_exception
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    AttachmentError,
    ContextBaseOverBudgetError,
    ContextInputTooLargeError,
    MalformedToolCallError,
)
from llm_providers import (
    LLMModelUnavailableError,
    LLMOutputBudgetExhaustedError,
    LLMPaymentRequiredError,
    LLMPermissionError,
    LLMStreamInterruptedAfterOutputError,
    default_model_runtime_config,
)
from runtime_config import (
    CredentialMissingError,
    DesktopSettingsReader,
    credential_source_for_mode,
)


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
    monkeypatch.delenv("AMAP_WEBSERVICE_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_HOST", raising=False)

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
        assert "recover_tool_result" in tool_names
        assert "nearby_place_search" in tool_names
        assert "todo_manage" in tool_names
        assert "social_search" in tool_names
        assert "social_read" in tool_names
        assert runtime.social_platform_service is not None
        assert runtime.todo_store is not None
        assert runtime.todo_store.path == tmp_path / "todos" / "todos.db"
        assert manager_registry is runtime.tool_registry
        assert (settings.personas_dir / "default" / "persona.md").is_file()
    finally:
        runtime.close()


def test_runtime_prompt_factory_reads_immersive_mode_for_next_snapshot(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"settings": {"immersiveMode": false}}',
        encoding="utf-8",
    )
    runtime = build_runtime(
        BridgeSettings(
            sessions_dir=tmp_path / "sessions",
            personas_dir=tmp_path / "personas",
            memory_enabled=False,
            im_enabled=False,
        ),
        desktop_settings_reader=DesktopSettingsReader(settings_path),
    )

    try:
        default_persona_id = runtime.catalog.find_by_name("default").id
        transparent_prompt = runtime.prompt_builder_factory(default_persona_id).build()
        assert "明确说明自己是 AI 或虚拟朋友" in transparent_prompt

        settings_path.write_text(
            '{"settings": {"immersiveMode": true}}',
            encoding="utf-8",
        )
        immersive_prompt = runtime.prompt_builder_factory(default_persona_id).build()
        assert "不主动说“作为 AI”" in immersive_prompt
        assert "明确说明自己是 AI 或虚拟朋友" not in immersive_prompt
    finally:
        runtime.close()


def test_memory_enabled_registers_history_and_structured_sources(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("AMAP_WEBSERVICE_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_HOST", raising=False)
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
        assert history_tool._search_service.has_source_order_provider is True
    finally:
        runtime.close()


def test_runtime_config_injects_amap_provider(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.setenv("AMAP_WEBSERVICE_KEY", "test-amap-key")
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
        tool = next(
            tool
            for tool in runtime.tool_registry.all_tools()
            if isinstance(tool, NearbyPlaceSearchTool)
        )
        assert isinstance(tool._provider, AmapNearbyPlaceProvider)
    finally:
        runtime.close()


def test_runtime_config_injects_qweather_provider(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.setenv("QWEATHER_API_KEY", "test-qweather-key")
    monkeypatch.setenv("QWEATHER_API_HOST", "test-api.qweatherapi.com")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("AMAP_WEBSERVICE_KEY", raising=False)

    runtime = build_runtime(
        BridgeSettings(
            sessions_dir=tmp_path / "sessions",
            personas_dir=tmp_path / "personas",
            memory_enabled=False,
            im_enabled=False,
        )
    )

    try:
        tool = next(
            tool for tool in runtime.tool_registry.all_tools() if isinstance(tool, WeatherQueryTool)
        )
        assert isinstance(tool._provider, QWeatherProvider)
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
            runtime.llm_client_factory(  # type: ignore[misc]
                default_model_runtime_config(runtime.default_model)
            )
    finally:
        runtime.close()


def test_missing_credentials_map_to_recoverable_protocol_error() -> None:
    error = map_exception(CredentialMissingError(["DEEPSEEK_API_KEY"]))

    assert error.http_status == 503
    assert error.code == "credential_missing"
    assert error.recoverable is True


def test_attachment_error_keeps_stable_protocol_code() -> None:
    error = map_exception(
        AttachmentError("attachment_context_too_large", "附件内容超过当前模型容量。")
    )

    assert error.http_status == 400
    assert error.code == "attachment_context_too_large"
    assert error.message == "附件内容超过当前模型容量。"


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ContextBaseOverBudgetError("too large"), "base_prompt_over_budget"),
        (ContextInputTooLargeError("too large"), "turn_input_too_large"),
        (LLMOutputBudgetExhaustedError("empty"), "output_budget_exhausted"),
    ],
)
def test_budget_failures_keep_specific_protocol_codes(exc: Exception, code: str) -> None:
    error = map_exception(exc)

    assert error.code == code
    assert error.http_status in {400, 502}


def test_post_output_stream_interruption_keeps_specific_protocol_code() -> None:
    error = map_exception(LLMStreamInterruptedAfterOutputError("interrupted"))

    assert error.http_status == 502
    assert error.code == "stream_interrupted_after_output"
    assert error.recoverable is True


def test_content_policy_error_has_honest_message_and_sanitizes_failed_call() -> None:
    from llm_providers import LLMContentPolicyError

    error = map_exception(LLMContentPolicyError("Content Exists Risk"))

    assert error.http_status == 400
    assert error.code == "content_policy"
    assert "安全限制" in error.message
    assert error.sanitize_failed_model_call is True


def test_malformed_tool_call_error_is_recoverable_and_honest() -> None:
    error = map_exception(MalformedToolCallError("exec_command: missing reason"))

    assert error.http_status == 502
    assert error.code == "malformed_tool_call"
    assert "工具没有执行，也没有发起授权" in error.message
    assert error.recoverable is True
    assert error.sanitize_failed_model_call is True


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (LLMPaymentRequiredError("quota"), 402, "payment_required"),
        (LLMPermissionError("policy"), 403, "permission_denied"),
        (
            LLMModelUnavailableError("no route", retryable=False),
            503,
            "model_route_unavailable",
        ),
    ],
)
def test_openrouter_errors_keep_actionable_protocol_categories(
    exc: Exception,
    status: int,
    code: str,
) -> None:
    error = map_exception(exc)

    assert error.http_status == status
    assert error.code == code


def test_memory_model_defaults_to_v4_pro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.delenv("DEEPSEEK_MEMORY_MODEL", raising=False)

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    assert runtime_config.memory_model == "deepseek/deepseek-v4-pro"


def test_memory_model_allows_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.setenv("DEEPSEEK_MEMORY_MODEL", "deepseek/custom-memory")

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    assert runtime_config.memory_model == "deepseek/custom-memory"


def test_runtime_config_allows_provider_timeout_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fake-key")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "12.5")

    captured: list[dict[str, Any]] = []

    def capture_model(**kwargs: Any) -> object:
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(
        bridge_runtime_config_module,
        "create_language_model",
        capture_model,
    )
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    runtime_config.memory_client_factory()
    runtime_config.llm_client_factory(default_model_runtime_config(runtime_config.default_model))

    assert [item["timeout_seconds"] for item in captured] == [12.5, 12.5]


def test_runtime_config_builds_kimi_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "test-kimi-key")
    monkeypatch.setenv("LLM_CHAT_PROVIDER", "kimi")
    monkeypatch.setenv("LLM_CHAT_MODEL", "kimi/kimi-k2.7-code-highspeed")
    monkeypatch.setenv("LLM_MEMORY_PROVIDER", "kimi")
    monkeypatch.setenv("LLM_MEMORY_MODEL", "kimi/kimi-k2.6")

    captured: list[dict[str, Any]] = []

    def capture_model(**kwargs: Any) -> object:
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(
        bridge_runtime_config_module,
        "create_language_model",
        capture_model,
    )
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("env-only"))
    runtime_config.memory_client_factory()
    runtime_config.llm_client_factory(default_model_runtime_config(runtime_config.default_model))

    assert runtime_config.default_model == "kimi/kimi-k2.7-code-highspeed"
    assert runtime_config.memory_model == "kimi/kimi-k2.6"
    assert [(item["provider_id"], item["model_id"], item["purpose"]) for item in captured] == [
        ("kimi", "kimi/kimi-k2.6", "memory"),
        ("kimi", "kimi/kimi-k2.7-code-highspeed", "chat"),
    ]
