"""``WebSearchTool`` 单元测试：通过 mock provider 验证错误分类映射。

不依赖真实 Tavily / 网络。验证：

- happy path：provider 返回 hits → ToolResult.is_error=False，meta 正确，正文格式化
- 4 类异常：每种都映射到 is_error=True 的 ToolResult，文案前缀正确
- 空 hits：返回 "没找到相关结果" 文案，is_error=False
"""

from __future__ import annotations

from typing import ClassVar

from agent.tools import ToolResult
from agent.tools.builtin.web_search import (
    SearchHit,
    WebSearchAuthError,
    WebSearchNetworkError,
    WebSearchProviderError,
    WebSearchRateLimitError,
    WebSearchTool,
)


class _StubProvider:
    """testing-only provider stub。不走 :class:`WebSearchProvider` runtime check
    时也能用——只要 attr/method 形态一致即可（duck typing）。"""

    name: ClassVar[str] = "stub"

    def __init__(
        self,
        *,
        hits: list[SearchHit] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._hits = hits or []
        self._raises = raises
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, max_results: int) -> list[SearchHit]:
        self.calls.append((query, max_results))
        if self._raises is not None:
            raise self._raises
        return self._hits


def test_happy_path_returns_formatted_text() -> None:
    hits = [
        SearchHit(title="标题 A", url="https://a.example/", snippet="摘要 A 内容"),
        SearchHit(title="标题 B", url="https://b.example/", snippet="摘要 B 内容"),
    ]
    provider = _StubProvider(hits=hits)
    tool = WebSearchTool(provider=provider, max_results=5)

    result = tool.invoke({"query": "今天的新闻"})

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert "今天的新闻" in result.text
    assert "标题 A" in result.text
    assert "https://a.example/" in result.text
    assert "标题 B" in result.text
    assert "请基于以上信息用自己的话回答用户" in result.text
    # provider 收到正确的参数
    assert provider.calls == [("今天的新闻", 5)]
    # meta 字段齐全
    assert result.meta is not None
    assert result.meta["result_count"] == 2
    assert result.meta["provider"] == "stub"
    assert isinstance(result.meta["duration_seconds"], float)


def test_empty_hits_returns_no_results_message() -> None:
    provider = _StubProvider(hits=[])
    tool = WebSearchTool(provider=provider)

    result = tool.invoke({"query": "no-result"})

    assert result.is_error is False
    assert "没找到相关结果" in result.text
    assert result.meta is not None
    assert result.meta["result_count"] == 0


def test_auth_error_maps_to_is_error_true() -> None:
    provider = _StubProvider(raises=WebSearchAuthError("invalid key"))
    tool = WebSearchTool(provider=provider)

    result = tool.invoke({"query": "x"})

    assert result.is_error is True
    assert "鉴权" in result.text
    # 错误文案不暴露 SDK 原文，避免泄漏内部细节给 LLM
    assert "invalid key" not in result.text
    # meta 不强制要求字段齐全（错误路径），不做断言


def test_rate_limit_error_maps_to_is_error_true() -> None:
    provider = _StubProvider(raises=WebSearchRateLimitError("quota exhausted"))
    tool = WebSearchTool(provider=provider)

    result = tool.invoke({"query": "x"})

    assert result.is_error is True
    assert "限流" in result.text


def test_network_error_maps_to_is_error_true() -> None:
    provider = _StubProvider(raises=WebSearchNetworkError("read timeout"))
    tool = WebSearchTool(provider=provider)

    result = tool.invoke({"query": "x"})

    assert result.is_error is True
    assert "网络" in result.text


def test_provider_error_includes_message() -> None:
    """``WebSearchProviderError`` 文案保留原文（区别于 auth/rate/network 三类
    标准化文案）：因为这类错往往是请求格式 / 未分类，原文有助于 LLM 决定要不要换 query。"""
    provider = _StubProvider(raises=WebSearchProviderError("malformed request"))
    tool = WebSearchTool(provider=provider)

    result = tool.invoke({"query": "x"})

    assert result.is_error is True
    assert "搜索失败" in result.text
    assert "malformed request" in result.text


def test_max_results_passed_through() -> None:
    provider = _StubProvider(hits=[])
    tool = WebSearchTool(provider=provider, max_results=3)

    tool.invoke({"query": "y"})

    assert provider.calls == [("y", 3)]
