"""``ToolRegistry``：工具名 → :class:`Tool` 的查找表。

构造期固化（本期不做"运行时动态注册 / 卸载"）。未来如 MCP 接入需要运行时拉新
工具，通过派生类或新方法扩展，不动现有契约。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.1.3。
"""

from __future__ import annotations

from typing import Any

from .errors import ToolDuplicateError, ToolNotFoundError
from .protocol import Tool, ToolResult


class ToolRegistry:
    """工具集合。

    Args:
        tools: 工具列表。构造时检查重名，重名直接抛 :class:`ToolDuplicateError`。

    Raises:
        ToolDuplicateError: ``tools`` 中存在 ``name`` 重复。
    """

    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            if t.name in self._tools:
                raise ToolDuplicateError(f"工具名重复: {t.name!r}")
            self._tools[t.name] = t

    def all_tools(self) -> list[Tool]:
        """返回所有已注册工具的列表（按构造时的插入顺序）。"""
        return list(self._tools.values())

    def get(self, name: str) -> Tool:
        """按名取工具。

        Raises:
            ToolNotFoundError: 该 name 未注册。
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"未注册的工具: {name!r}")
        return self._tools[name]

    def invoke(self, name: str, args: dict[str, Any]) -> ToolResult:
        """执行指定工具。

        Args:
            name: 工具名。
            args: 入参字典。

        Returns:
            :class:`ToolResult`。

        Raises:
            ToolNotFoundError: 该 name 未注册。
            Exception: 工具内部协议级异常会原样向上抛（业务级失败应是
                ``ToolResult(is_error=True)``，不会 raise）。
        """
        return self.get(name).invoke(args)

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """转换为 OpenAI tool calling API 要求的 ``tools`` 数组。

        每个工具序列化为
        ``{"type": "function", "function": {"name", "description", "parameters"}}``。

        Returns:
            可直接传给 :meth:`llm_providers.LLMClient.stream` 的 ``tools`` 参数。
            空 registry 返回空列表。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema,
                },
            }
            for t in self._tools.values()
        ]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __repr__(self) -> str:  # pragma: no cover
        names = ", ".join(self._tools.keys())
        return f"ToolRegistry({names})"
