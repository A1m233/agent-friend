"""``make_default_registry`` 工厂单测：

- 未配置 ``TAVILY_API_KEY`` → 返回空 registry，stderr 给提示
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
from agent.tools.builtin.web_search import WebSearchTool
from agent.tools.builtin.web_search.providers.tavily import TavilyProvider
from memory.search import MemorySearchService


def test_returns_empty_registry_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry()

    assert isinstance(registry, ToolRegistry)
    assert {tool.name for tool in registry.all_tools()} == {"calculate", "datetime_calculate"}
    captured = capsys.readouterr()
    # 提示走 stderr
    assert "TAVILY_API_KEY" in captured.err
    assert "搜索能力已关闭" in captured.err


def test_silent_when_no_api_key_and_warn_disabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry(stderr_warn=False)

    assert {tool.name for tool in registry.all_tools()} == {"calculate", "datetime_calculate"}
    captured = capsys.readouterr()
    assert captured.err == ""


def test_returns_registry_with_web_search_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-test-key")

    registry = make_default_registry()

    tools = registry.all_tools()
    assert len(tools) == 4
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
    assert len(tools) == 4
    assert isinstance(registry.get("web_search"), WebSearchTool)


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
    assert len(tools) == 3
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
