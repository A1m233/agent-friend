"""``make_default_registry`` 工厂单测：

- 未配置地图 Key → 仍注册可返回配置提示的地点工具
- 已配置 ``TAVILY_API_KEY`` → 返回含 ``web_search`` 的 registry，注入的 provider 是 Tavily
- 020 起：``session_store`` 非 ``None`` 时额外注册 ``ConversationHistoryTool``；
  ``None`` 时行为与 005 完全一致（向后兼容）

所有用例都用 monkeypatch 隔离 env，不读真实 ``.env``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent.sessions.store import NullSessionStore
from agent.todos import TodoService, TodoStore
from agent.tools import ToolRegistry, make_default_registry
from agent.tools.builtin.conversation_history import ConversationHistoryTool
from agent.tools.builtin.nearby_place_search import (
    NearbyPlaceSearchTool,
    UnavailableNearbyPlaceProvider,
)
from agent.tools.builtin.nearby_place_search.providers import AmapNearbyPlaceProvider
from agent.tools.builtin.social_platforms import (
    RequestSocialAuthTool,
    SocialPlatformService,
    SocialReadTool,
    SocialSearchTool,
)
from agent.tools.builtin.weather_query import (
    UnavailableWeatherProvider,
    WeatherQueryTool,
)
from agent.tools.builtin.weather_query.providers import QWeatherProvider
from agent.tools.builtin.web_search import WebSearchTool
from agent.tools.builtin.web_search.providers.tavily import TavilyProvider
from memory.search import MemorySearchService


@pytest.fixture(autouse=True)
def _isolate_tool_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMAP_WEBSERVICE_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_HOST", raising=False)


def test_registers_unavailable_nearby_tool_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry()

    assert isinstance(registry, ToolRegistry)
    tools = registry.all_tools()
    assert {tool.name for tool in tools} == {
        "calculate",
        "datetime_calculate",
        "nearby_place_search",
        "weather_query",
        "social_search",
        "social_read",
        "request_social_auth",
        "web_fetch",
    }
    nearby = registry.get("nearby_place_search")
    assert isinstance(nearby, NearbyPlaceSearchTool)
    assert isinstance(nearby._provider, UnavailableNearbyPlaceProvider)
    weather = registry.get("weather_query")
    assert isinstance(weather, WeatherQueryTool)
    assert isinstance(weather._provider, UnavailableWeatherProvider)
    captured = capsys.readouterr()
    assert "TAVILY_API_KEY" in captured.err
    assert "网页搜索和远端正文回退已关闭" in captured.err
    assert "AMAP_WEBSERVICE_KEY" in captured.err

    result = nearby.invoke({"query": "药店", "near": "福民银行"})
    assert result.is_error is True
    assert "configuration_required" in result.text


def test_silent_when_no_api_key_and_warn_disabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry(stderr_warn=False)

    assert {tool.name for tool in registry.all_tools()} == {
        "calculate",
        "datetime_calculate",
        "nearby_place_search",
        "weather_query",
        "social_search",
        "social_read",
        "request_social_auth",
        "web_fetch",
    }
    captured = capsys.readouterr()
    assert captured.err == ""


def test_returns_registry_with_web_search_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-test-key")

    registry = make_default_registry()

    tools = registry.all_tools()
    assert len(tools) == 9
    tool = registry.get("web_search")
    assert isinstance(tool, WebSearchTool)
    assert tool.name == "web_search"
    # provider 注入是 Tavily 实现
    # （不依赖具体属性名，用 isinstance 校验类型）
    assert isinstance(tool._provider, TavilyProvider)


def test_explicit_tavily_key_registers_web_search_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry(tavily_api_key="test-tavily-explicit")

    tools = registry.all_tools()
    assert len(tools) == 9
    assert isinstance(registry.get("web_search"), WebSearchTool)


def test_explicit_amap_key_registers_real_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry(
        amap_webservice_key="test-amap-explicit",
        stderr_warn=False,
    )

    tool = registry.get("nearby_place_search")
    assert isinstance(tool, NearbyPlaceSearchTool)
    assert isinstance(tool._provider, AmapNearbyPlaceProvider)


def test_explicit_qweather_credentials_register_real_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry(
        qweather_api_key="test-qweather-explicit",
        qweather_api_host="test-api.qweatherapi.com",
        stderr_warn=False,
    )

    tool = registry.get("weather_query")
    assert isinstance(tool, WeatherQueryTool)
    assert isinstance(tool._provider, QWeatherProvider)


# ===== 020 新增：session_store 注入 =====


def test_no_session_store_does_not_register_history_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``session_store=None``（默认）→ ``ConversationHistoryTool`` 不被注册。

    保证 005 ~ 019 的 web-search-only 调用方代码不传 ``session_store`` 时
    行为完全一致。
    """
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry()

    assert all(not isinstance(t, ConversationHistoryTool) for t in registry.all_tools())


def test_session_store_injection_registers_history_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry(session_store=NullSessionStore())

    tools = registry.all_tools()
    assert len(tools) == 9
    history = registry.get("recall_past_chats")
    assert isinstance(history, ConversationHistoryTool)


def test_both_key_and_store_registers_both_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")

    registry = make_default_registry(session_store=NullSessionStore())

    tools = registry.all_tools()
    names = {t.name for t in tools}
    assert names == {
        "calculate",
        "datetime_calculate",
        "web_search",
        "web_fetch",
        "nearby_place_search",
        "weather_query",
        "recall_past_chats",
        "social_search",
        "social_read",
        "request_social_auth",
    }


def test_explicit_memory_search_service_is_shared_with_history_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    service = MemorySearchService()

    registry = make_default_registry(memory_search=service)

    tool = registry.get("recall_past_chats")
    assert isinstance(tool, ConversationHistoryTool)
    assert tool._search_service is service


def test_todo_service_registers_todo_manage(tmp_path: Path) -> None:
    registry = make_default_registry(
        todo_service=TodoService(TodoStore(tmp_path / "todos.db")),
        stderr_warn=False,
    )
    assert "todo_manage" in registry


def test_social_tools_share_explicit_service() -> None:
    service = SocialPlatformService(())

    registry = make_default_registry(
        social_platform_service=service,
        stderr_warn=False,
    )

    search = registry.get("social_search")
    read = registry.get("social_read")
    auth = registry.get("request_social_auth")
    assert isinstance(search, SocialSearchTool)
    assert isinstance(read, SocialReadTool)
    assert isinstance(auth, RequestSocialAuthTool)
    assert search._service is service
    assert read._service is service
    assert auth._service is service
