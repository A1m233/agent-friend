"""``WebSearchTool``：对 LLM 暴露的"互联网搜索"能力。

本模块定义 web_search 子领域的对外接口集：

- :class:`SearchHit` — 单条搜索结果的数据结构（provider 解耦）
- :class:`WebSearchProvider` — 搜索 provider 的最小契约（仅 web_search 内部消费）
- :class:`WebSearchTool` — 实现 :class:`agent.tools.Tool` Protocol 的工具类
- :class:`WebSearchError` 体系 — provider 实现统一向外抛的错误类型

``WebSearchTool`` 不依赖任何具体 provider，通过构造期注入 :class:`WebSearchProvider`
解耦。具体 provider 实现见 :mod:`.providers` 子包。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.7。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable

from ...errors import ToolError
from ...protocol import ToolResult

# ===== 异常体系：provider 实现统一向外抛这些 =====


class WebSearchError(ToolError):
    """所有 :mod:`web_search` 模块异常的基类。继承自 :class:`ToolError` 体系。"""


class WebSearchAuthError(WebSearchError):
    """API key 错或失效。"""


class WebSearchRateLimitError(WebSearchError):
    """触达 provider 的限流 / 配额 / 临时禁用。"""


class WebSearchNetworkError(WebSearchError):
    """网络错或超时。"""


class WebSearchProviderError(WebSearchError):
    """其它 provider 侧错（含未分类）。"""


# ===== 数据结构 =====


@dataclass(frozen=True)
class SearchHit:
    """单条搜索结果的 provider 解耦表达。

    Attributes:
        title: 结果标题。
        url: 结果 URL。
        snippet: 摘要 / 正文片段。

    Note:
        所有 :class:`WebSearchProvider` 实现都返回 ``list[SearchHit]``——
        把 Tavily / Bocha / 智谱等各自的原生响应规范化到同一形态，
        :class:`WebSearchTool` 内部的 :func:`_format` 才能 provider-agnostic。
    """

    title: str
    url: str
    snippet: str


# ===== Provider 协议 =====


@runtime_checkable
class WebSearchProvider(Protocol):
    """搜索 provider 的最小契约。

    Note:
        本 Protocol **仅 web_search 子包内部消费**，不暴露到 ``agent.tools``
        公共 API（详见 005 design §6.1）——引擎层只看到 :class:`Tool` /
        :class:`ToolRegistry`，``WebSearchProvider`` 是 web_search 模块的
        实现细节。

        所有 provider 实现负责把各自 SDK 的异常 catch 后映射成本模块自有
        :class:`WebSearchError` 子类向上抛。
    """

    name: ClassVar[str]
    """provider 标识，写入 :class:`ToolResult` ``meta["provider"]`` 便于观测。"""

    def search(self, query: str, max_results: int) -> list[SearchHit]:
        """同步发起搜索请求。

        Args:
            query: 搜索查询字符串。
            max_results: 期望返回的最大结果数；provider 实际返回数可少于此值。

        Returns:
            按 provider 给出的相关性排序的 :class:`SearchHit` 列表。无结果时返回空列表。

        Raises:
            WebSearchAuthError: API key 错或失效
            WebSearchRateLimitError: 触达限流 / 配额
            WebSearchNetworkError: 网络错或超时
            WebSearchProviderError: 其它 provider 侧错
        """
        ...


# ===== Tool 主体 =====


class WebSearchTool:
    """对 LLM 暴露的"互联网搜索"工具，实现 :class:`agent.tools.Tool` Protocol。

    LLM 永远只看到 ``name="web_search"``——切 / 加 provider 不改这三件套，
    LLM 的工具选择行为不受 provider 替换影响。

    Args:
        provider: :class:`WebSearchProvider` 实例。由 :func:`agent.tools.make_default_registry`
            根据当前配置（env var）挑出对应 provider 注入。
        max_results: 期望返回的最大结果数，默认 5。
    """

    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "搜索互联网获取实时或可能变化的信息。\n\n"
        "**何时使用**：\n"
        "- 用户问及任何时效性信息：新闻、天气、汇率、股价、赛事结果\n"
        "- 用户问及『今天』『现在』『最新』『最近』『目前』等时效词相关的内容\n"
        "- 涉及具体日期 / 时间点之后发生的事\n"
        "- 涉及具体公司、产品、人物、作品、视频、梗、活动、政策或数字细节，且你不能 100% 确定\n\n"
        "**何时不要使用**：\n"
        "- 用户问的是你和用户过去是否聊过、推荐过或约定过；这类问题用历史对话工具核对\n"
        "- 用户要的是创作、改写、代码解释、情绪陪伴或明确不需要外部资料的判断\n"
        "- 当前对话里已经有用户提供的事实，且用户只是让你基于这些信息整理或推理\n\n"
        "**参数来源与证据边界**：\n"
        "- query 用清晰自然语言描述要找的信息，关键词 2~5 个，不超过一句话\n"
        "- query 必须包含正确的当前年份（参见 system prompt 中『当前时间』），不要默认用训练数据时期的年份\n"
        "- 搜索结果只证明外部资料里出现过这些内容，不能证明你过去和用户聊过\n"
        "- 只使用结果里实际出现的字段；标题、片段或来源没有给出的细节不要补\n\n"
        "**失败与重试**：\n"
        "- 结果不相关或没命中时，可换 query 重试 1~2 次\n"
        "- 仍没命中就说没直接找到，向用户要平台、时间、画面或其他线索\n"
        "- 不要对用户说 provider、API、SDK、鉴权、限流、stack trace 等技术字眼"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "搜索查询。用清晰的自然语言描述要找的信息，"
                    "**必须包含正确的当前年份**（参见 system prompt 中『当前时间』）。"
                ),
            },
        },
        "required": ["query"],
    }

    def __init__(self, provider: WebSearchProvider, max_results: int = 5) -> None:
        self._provider = provider
        self._max_results = max_results

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        query = args["query"]
        start = time.monotonic()
        try:
            hits = self._provider.search(query, self._max_results)
        except WebSearchAuthError:
            return ToolResult(text="搜索失败：API 鉴权错误", is_error=True)
        except WebSearchRateLimitError:
            return ToolResult(text="搜索失败：触达限流", is_error=True)
        except WebSearchNetworkError:
            return ToolResult(text="搜索失败：网络错误", is_error=True)
        except WebSearchProviderError as exc:
            return ToolResult(text=f"搜索失败：{exc}", is_error=True)

        text = _format(query, hits)
        duration = time.monotonic() - start
        return ToolResult(
            text=text,
            is_error=False,
            meta={
                "duration_seconds": duration,
                "result_count": len(hits),
                "provider": self._provider.name,
            },
        )


def _format(query: str, hits: list[SearchHit]) -> str:
    """把 :class:`SearchHit` 列表拼成喂给 LLM 的纯文本。

    Provider 无关——任何 provider 返回的 hits 都经过同一格式化逻辑，
    便于未来加新 provider 时复用 + 视觉一致。

    末尾追加一条 inline reminder 引导 LLM 用拟人化方式整合结果（与 product
    vision §3.1 的"像真人朋友"原则一致），不要把搜索结果原文直接贴给用户。
    """
    if not hits:
        return f"对 '{query}' 的搜索没找到相关结果。"
    lines = [f"对 '{query}' 的搜索结果（top {len(hits)}）："]
    for i, h in enumerate(hits, 1):
        snippet = h.snippet[:500]
        lines.append(f"\n[{i}] {h.title}\nURL: {h.url}\n{snippet}")
    lines.append(
        "\n\n请基于以上信息用自己的话回答用户。如果引用了具体内容，"
        '用拟人化方式提及来源（例如"我在新闻里看到..."），不要直接贴 URL。'
    )
    return "\n".join(lines)
