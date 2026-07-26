"""``make_default_registry``：根据当前环境配置组装默认 :class:`ToolRegistry`。

未来加新 provider / 新工具时改这里**一处**——挑选逻辑集中，``WebSearchTool`` /
:class:`Tool` Protocol / 引擎调用循环 / CLI 全部 0 改动。

详见:

- docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.7.5（``web_search``）
- docs/requirements/020-engine-tool-conversation-history/design.md §4.5（``conversation_history``）
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from memory.search import MemorySearchService, QueryAnalyzer

from ..memory_search import SessionHistorySource
from .builtin.calculate import CalculateTool
from .builtin.conversation_history import ConversationHistoryTool
from .builtin.datetime_calculate import DateTimeCalculateTool
from .builtin.web_fetch import TavilyExtractProvider, WebFetchTool
from .builtin.web_search import WebSearchTool
from .builtin.web_search.providers.tavily import TavilyProvider
from .protocol import Tool
from .registry import ToolRegistry

if TYPE_CHECKING:
    from ..sessions.store import SessionStore


def make_default_registry(
    session_store: SessionStore | None = None,
    *,
    memory_search: MemorySearchService | None = None,
    tavily_api_key: str | None = None,
    stderr_warn: bool = True,
) -> ToolRegistry:
    """构造默认 registry，确定性本地工具始终注册，网络/记忆工具按资源注册。

    Args:
        session_store: 020 起新增。非 ``None`` 时注册 :class:`ConversationHistoryTool`
            让 LLM 能主动回忆过往对话；``None``（默认）时不注册——保留 005 ~ 019
            的 web-search-only 行为完全字节兼容。``NullSessionStore`` 等空 store
            可注入：本工具调用时 ``list()`` 返回空，自然走拟人化"翻不到"兜底
            （详见 020 design §5.2 N-4）。
        tavily_api_key: Tavily API key。新桌面主路径由 runtime config 显式注入；
            ``None`` 时保留旧的 process env fallback。
        stderr_warn: 未配置 ``TAVILY_API_KEY`` 时是否往 stderr 打一行提示。
            默认开启；测试场景可传 ``False`` 关闭以避免日志噪声。

    Returns:
        :class:`ToolRegistry`：

        - 始终包含 ``calculate`` 与 ``datetime_calculate``
        - ``TAVILY_API_KEY`` 已配置 → 含 ``web_search`` 与 ``web_fetch``
        - ``session_store`` 非 ``None`` → 含 :class:`ConversationHistoryTool`
        - 网络与记忆条件都不满足 → 仍保留两个本地确定性工具
    """
    tools: list[Tool] = [CalculateTool(), DateTimeCalculateTool()]

    api_key = tavily_api_key or os.environ.get("TAVILY_API_KEY")
    if api_key:
        provider = TavilyProvider(api_key=api_key)
        tools.append(WebSearchTool(provider=provider))
        tools.append(WebFetchTool(provider=TavilyExtractProvider(api_key=api_key)))
    elif stderr_warn:
        print(
            "[tool] 未配置 TAVILY_API_KEY，搜索能力已关闭。"
            "如需启用，请运行 scripts/credentials/run.* set-bundle tavily；"
            "存量脚本可显式使用 legacy-dotenv。",
            file=sys.stderr,
        )

    if memory_search is None and session_store is not None:
        memory_search = MemorySearchService(
            [SessionHistorySource(session_store)],
            analyzer=QueryAnalyzer(),
        )
    if memory_search is not None:
        tools.append(ConversationHistoryTool(search_service=memory_search))

    return ToolRegistry(tools)
