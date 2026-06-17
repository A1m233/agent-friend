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

from .builtin.conversation_history import ConversationHistoryTool
from .builtin.web_search import WebSearchTool
from .builtin.web_search.providers.tavily import TavilyProvider
from .protocol import Tool
from .registry import ToolRegistry

if TYPE_CHECKING:
    from ..sessions.store import SessionStore


def make_default_registry(
    session_store: SessionStore | None = None,
    *,
    stderr_warn: bool = True,
) -> ToolRegistry:
    """构造默认 registry。本期按当前可用资源装配两个内置工具。

    Args:
        session_store: 020 起新增。非 ``None`` 时注册 :class:`ConversationHistoryTool`
            让 LLM 能主动回忆过往对话；``None``（默认）时不注册——保留 005 ~ 019
            的 web-search-only 行为完全字节兼容。``NullSessionStore`` 等空 store
            可注入：本工具调用时 ``list()`` 返回空，自然走拟人化"翻不到"兜底
            （详见 020 design §5.2 N-4）。
        stderr_warn: 未配置 ``TAVILY_API_KEY`` 时是否往 stderr 打一行提示。
            默认开启；测试场景可传 ``False`` 关闭以避免日志噪声。

    Returns:
        :class:`ToolRegistry`：

        - ``TAVILY_API_KEY`` 已配置 → 含 :class:`WebSearchTool`（Tavily provider）
        - ``session_store`` 非 ``None`` → 含 :class:`ConversationHistoryTool`
        - 两条都不满足 → 空 registry（保证未配置工具的用户也能正常使用基础对话）
    """
    tools: list[Tool] = []

    api_key = os.environ.get("TAVILY_API_KEY")
    if api_key:
        provider = TavilyProvider(api_key=api_key)
        tools.append(WebSearchTool(provider=provider))
    elif stderr_warn:
        print(
            "[tool] 未配置 TAVILY_API_KEY，搜索能力已关闭。"
            "如需启用，把 key 加入 .env（详见 .env.example）。",
            file=sys.stderr,
        )

    if session_store is not None:
        tools.append(ConversationHistoryTool(store=session_store))

    return ToolRegistry(tools)
