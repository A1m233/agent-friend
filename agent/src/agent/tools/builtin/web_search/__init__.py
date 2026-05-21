"""``web_search`` —— 互联网搜索工具子包。

对外仅暴露 :class:`WebSearchTool`（实现 :class:`agent.tools.Tool` Protocol）。
具体 provider 实现集中在 :mod:`.providers` 子包下，``WebSearchTool`` 不感知
任何 provider 细节，通过构造期注入 :class:`WebSearchProvider` 解耦。

未来加新 provider（如 Bocha / 智谱搜索 / 自部署 SearXNG）时：

- 新增 ``providers/<name>.py``，在其中实现 :class:`WebSearchProvider`
- 在 :func:`agent.tools.make_default_registry` 工厂里加挑选逻辑
- ``WebSearchTool`` / Tool Protocol / 引擎层 0 改动

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.7。
"""

from .tool import (
    SearchHit,
    WebSearchAuthError,
    WebSearchError,
    WebSearchNetworkError,
    WebSearchProvider,
    WebSearchProviderError,
    WebSearchRateLimitError,
    WebSearchTool,
)

__all__ = [
    "SearchHit",
    "WebSearchAuthError",
    "WebSearchError",
    "WebSearchNetworkError",
    "WebSearchProvider",
    "WebSearchProviderError",
    "WebSearchRateLimitError",
    "WebSearchTool",
]
