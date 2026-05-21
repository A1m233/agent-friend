"""``make_default_registry``：根据当前环境配置组装默认 :class:`ToolRegistry`。

未来加新 provider / 新工具时改这里**一处**——挑选逻辑集中，``WebSearchTool`` /
:class:`Tool` Protocol / 引擎调用循环 / CLI 全部 0 改动。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.7.5。
"""

from __future__ import annotations

import os
import sys

from .builtin.web_search import WebSearchTool
from .builtin.web_search.providers.tavily import TavilyProvider
from .registry import ToolRegistry


def make_default_registry(*, stderr_warn: bool = True) -> ToolRegistry:
    """构造默认 registry。本期硬选 Tavily。

    Args:
        stderr_warn: 未配置 ``TAVILY_API_KEY`` 时是否往 stderr 打一行提示。
            默认开启；测试场景可传 ``False`` 关闭以避免日志噪声。

    Returns:
        :class:`ToolRegistry`：

        - ``TAVILY_API_KEY`` 已配置 → 含 :class:`WebSearchTool`（Tavily provider）的 registry
        - 未配置 → 空 registry（保证未配置 key 的用户也能正常使用基础对话）
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        if stderr_warn:
            print(
                "[tool] 未配置 TAVILY_API_KEY，搜索能力已关闭。"
                "如需启用，把 key 加入 .env（详见 .env.example）。",
                file=sys.stderr,
            )
        return ToolRegistry([])

    provider = TavilyProvider(api_key=api_key)
    web_search = WebSearchTool(provider=provider)
    return ToolRegistry([web_search])
