"""``make_default_registry`` 工厂单测：

- 未配置地图 Key → 仍注册可返回配置提示的地点工具
- 已配置 ``TAVILY_API_KEY`` → 返回含 ``web_search`` 的 registry，注入的 provider 是 Tavily
- 020 起：``session_store`` 非 ``None`` 时额外注册 ``ConversationHistoryTool``；
  ``None`` 时行为与 005 完全一致（向后兼容）

所有用例都用 monkeypatch 隔离 env，不读真实 ``.env``。
"""

from __future__ import annotations

import pytest
from agent.sessions.store import NullSessionStore
from agent.tools import ToolRegistry, make_default_registry
from agent.tools.builtin.conversation_history import ConversationHistoryTool
from agent.tools.builtin.nearby_place_search import (
    NearbyPlaceSearchTool,
    UnavailableNearbyPlaceProvider,
)
from agent.tools.builtin.nearby_place_search.providers import AmapNearbyPlaceProvider
from agent.tools.builtin.web_search import WebSearchTool
from agent.tools.builtin.web_search.providers.tavily import TavilyProvider
from memory.search import MemorySearchService


@pytest.fixture(autouse=True)
def _isolate_map_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMAP_WEBSERVICE_KEY", raising=False)


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
    }
    nearby = registry.get("nearby_place_search")
    assert isinstance(nearby, NearbyPlaceSearchTool)
    assert isinstance(nearby._provider, UnavailableNearbyPlaceProvider)
    captured = capsys.readouterr()
    assert "TAVILY_API_KEY" in captured.err
    assert "搜索能力已关闭" in captured.err
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
    }
    captured = capsys.readouterr()
    assert captured.err == ""


def test_returns_registry_with_web_search_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-test-key")

    registry = make_default_registry()

    tools = registry.all_tools()
    assert len(tools) == 5
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
    assert len(tools) == 5
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
    assert len(tools) == 4
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
        "recall_past_chats",
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
