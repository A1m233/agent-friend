"""Tavily Search API 的 :class:`WebSearchProvider` 实现。

把 Tavily SDK 的原生异常映射成 :mod:`web_search` 模块自有异常向上抛，
让 :class:`WebSearchTool.invoke` 不感知 Tavily SDK 的存在。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.7.3。
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..tool import (
    SearchHit,
    WebSearchAuthError,
    WebSearchNetworkError,
    WebSearchProviderError,
    WebSearchRateLimitError,
)


class TavilyProvider:
    """Tavily Search API 的 :class:`WebSearchProvider` 实现。

    Args:
        api_key: Tavily API key（``tvly-...``）。由
            :func:`agent.tools.make_default_registry` 工厂从 env var
            ``TAVILY_API_KEY`` 读取后注入；本类不再读 env，便于独立测试 /
            未来多账号场景。

    Note:
        本类**构造期不发起任何网络请求**——按 0002 §3.4"启动期 fail-fast
        基础设施"原则，构造期仅校验 ``api_key`` 非空。Tavily 客户端
        (``TavilyClient``) 在 :meth:`search` 调用时按需实例化。
    """

    name: ClassVar[str] = "tavily"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise WebSearchAuthError("TAVILY_API_KEY 为空")
        self._api_key = api_key

    def search(self, query: str, max_results: int) -> list[SearchHit]:
        from tavily import TavilyClient
        from tavily.errors import (
            BadRequestError,
            ForbiddenError,
            InvalidAPIKeyError,
            MissingAPIKeyError,
            UsageLimitExceededError,
        )
        from tavily.errors import TimeoutError as TavilyTimeoutError

        try:
            client = TavilyClient(api_key=self._api_key)
            resp = client.search(query=query, max_results=max_results)
        except (InvalidAPIKeyError, MissingAPIKeyError) as e:
            raise WebSearchAuthError(str(e)) from e
        except (UsageLimitExceededError, ForbiddenError) as e:
            raise WebSearchRateLimitError(str(e)) from e
        except TavilyTimeoutError as e:
            raise WebSearchNetworkError(str(e)) from e
        except BadRequestError as e:
            raise WebSearchProviderError(f"请求格式错: {e}") from e
        except Exception as e:
            # 兜底：连接错 / DNS 错 / 未预期的 SDK 异常一律走 provider error
            # （网络异常的具体类型受 tavily 内部实现影响，统一兜底更稳）
            msg = str(e).lower()
            if "timeout" in msg or "connect" in msg or "network" in msg:
                raise WebSearchNetworkError(str(e)) from e
            raise WebSearchProviderError(str(e)) from e

        return _to_search_hits(resp)


def _to_search_hits(resp: dict[str, Any]) -> list[SearchHit]:
    """把 Tavily 原生响应规范化为 :class:`SearchHit` 列表。

    Tavily ``search`` 响应结构（截至 2026/05）：

    ``{"query": str, "results": [{"title": str, "url": str, "content": str, ...}, ...]}``
    """
    return [
        SearchHit(
            title=r.get("title", "") or "",
            url=r.get("url", "") or "",
            snippet=r.get("content", "") or "",
        )
        for r in resp.get("results", [])
    ]
