"""``ToolRegistry``：工具名 → :class:`Tool` 的查找表。

构造期固化（本期不做"运行时动态注册 / 卸载"）。未来如 MCP 接入需要运行时拉新
工具，通过派生类或新方法扩展，不动现有契约。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.1.3。
"""

from __future__ import annotations

from collections.abc import Collection
from html import escape
from typing import Any

from .errors import ToolDuplicateError, ToolNotFoundError
from .identity import ToolId, tool_id_from_implementation
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
        self._tool_ids: dict[str, ToolId] = {}
        for t in tools:
            if t.name in self._tools:
                raise ToolDuplicateError(f"工具名重复: {t.name!r}")
            tool_id = tool_id_from_implementation(t)
            self._tools[t.name] = t
            self._tool_ids[t.name] = tool_id

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

    def identity(self, name: str) -> ToolId:
        """Return the first-class namespace/name identity for a registered tool."""
        if name not in self._tool_ids:
            raise ToolNotFoundError(f"未注册的工具: {name!r}")
        return self._tool_ids[name]

    def all_identities(self) -> list[ToolId]:
        """Return identities in the same deterministic order as :meth:`all_tools`."""
        return list(self._tool_ids.values())

    def result_context_retention(self, name: str) -> str:
        """返回工具结果的上下文保留策略；未知或未声明工具默认 pinned。"""
        tool = self._tools.get(name)
        policy = getattr(tool, "result_context_retention", "pinned")
        return policy if policy in {"recoverable", "pinned"} else "pinned"

    def render_capability_catalog(
        self,
        *,
        callable_names: Collection[str] | None = None,
    ) -> str:
        """Render trusted namespace ownership for the tools visible in one model call.

        Provider function calling remains flat for OpenAI-compatible models.  The
        catalog is the lossless model-facing projection of the host's first-class
        namespace relation; ``callable_as`` stays equal to the existing wire name.
        """
        allowed = set(callable_names) if callable_names is not None else None
        grouped: dict[str, list[ToolId]] = {}
        for tool_id in self._tool_ids.values():
            if tool_id.namespace is None:
                continue
            if allowed is not None and tool_id.name not in allowed:
                continue
            grouped.setdefault(tool_id.namespace, []).append(tool_id)
        if not grouped:
            return ""

        lines = [
            "<tool_capability_catalog>",
            "  <instruction>namespace 是工具相对于内置工具根目录的可信模块路径；"
            "调用时仍使用 callable_as。</instruction>",
        ]
        for namespace in sorted(grouped):
            lines.append(f'  <namespace path="{escape(namespace, quote=True)}">')
            for tool_id in sorted(grouped[namespace], key=lambda item: item.name):
                escaped_name = escape(tool_id.name, quote=True)
                lines.append(f'    <tool name="{escaped_name}" callable_as="{escaped_name}" />')
            lines.append("  </namespace>")
        lines.append("</tool_capability_catalog>")
        return "\n".join(lines)

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
