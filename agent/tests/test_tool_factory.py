"""``make_default_registry`` 工厂单测：

- 未配置 ``TAVILY_API_KEY`` → 返回空 registry，stderr 给提示
- 已配置 ``TAVILY_API_KEY`` → 返回含 ``web_search`` 的 registry，注入的 provider 是 Tavily

所有用例都用 monkeypatch 隔离 env，不读真实 ``.env``。
"""

from __future__ import annotations

import pytest
from agent.tools import ToolRegistry, make_default_registry
from agent.tools.builtin.web_search import WebSearchTool
from agent.tools.builtin.web_search.providers.tavily import TavilyProvider


def test_returns_empty_registry_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry()

    assert isinstance(registry, ToolRegistry)
    assert registry.all_tools() == []
    captured = capsys.readouterr()
    # 提示走 stderr
    assert "TAVILY_API_KEY" in captured.err
    assert "搜索能力已关闭" in captured.err


def test_silent_when_no_api_key_and_warn_disabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    registry = make_default_registry(stderr_warn=False)

    assert registry.all_tools() == []
    captured = capsys.readouterr()
    assert captured.err == ""


def test_returns_registry_with_web_search_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-test-key")

    registry = make_default_registry()

    tools = registry.all_tools()
    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, WebSearchTool)
    assert tool.name == "web_search"
    # provider 注入是 Tavily 实现
    # （不依赖具体属性名，用 isinstance 校验类型）
    assert isinstance(tool._provider, TavilyProvider)
