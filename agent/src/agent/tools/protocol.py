"""``Tool`` Protocol 与 ``ToolResult``：工具调用最小契约。

定义引擎层"工具"的对外协议形态。具体实现可以是内置 Python 类（如
``WebSearchTool``）、未来的 MCP 桥接、远端 RPC 工具等。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.1.1 / §4.1.2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolResult:
    """工具执行的统一返回结构。

    Attributes:
        text: 喂回 LLM 的纯文本（即使 tool 内部产生结构化数据，也要在这里
            序列化成文本）。协议上 OpenAI / Anthropic 的 ``tool_result.content``
            必须是字符串。
        is_error: ``True`` 表示业务级失败（网络错 / key 错 / 限流等）；
            ``False`` 表示正常结果。失败时 ``text`` 仍要给出失败描述，
            交由 LLM 决定是否再试或换路。
        meta: 供观测 / 日志用的额外信息（如耗时、result 数量、原始 provider
            错误码）。**不会喂回 LLM**。CLI 可以读它做更精细的可视化。
    """

    text: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Tool(Protocol):
    """单个工具的最小契约。

    Attributes:
        name: 工具名（在同一个 :class:`ToolRegistry` 内唯一；建议 snake_case）。
        description: 给 LLM 看的简短描述（一句话讲清楚做什么、何时用）。
        parameters_schema: JSON Schema (draft-07) 描述 :meth:`invoke` 入参；
            遵循 OpenAI tool calling spec 中 ``function.parameters`` 的格式。

    Note:
        本期同步接口（不是 ``async``），与 0002 §3.4 选定的"同步优先"基调一致。
        未来若引入异步工具（如长任务、流式日志输出），通过新增可选方法
        ``async_invoke`` 扩展，**不破坏现有同步实现**。

        Protocol 上**不绑定 UI 渲染**（无 ``render_xxx`` / ``user_facing_name``
        等方法）——UI 渲染统一由 CLI / 前端基于 ``ConversationEvent`` 决定。
        Protocol 上**不绑定权限**——本期不做沙箱 / 用户开关；未来真有需求时
        通过新增可选方法扩展。

        三个属性用 :class:`typing.ClassVar` 标注——Tool 实现的 ``name`` /
        ``description`` / ``parameters_schema`` 都是定义级常量（同类所有实例共享），
        不应被实例化时 mutate。
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters_schema: ClassVar[dict[str, Any]]

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        """同步执行工具。

        Args:
            args: 已由 LLM 按 ``parameters_schema`` 生成的入参字典。
                注意：本期**不强制**实现方做二次 schema 校验，
                信任 LLM 的输出；未来如需严格校验由各 Tool 自行实现。

        Returns:
            统一的 :class:`ToolResult`。

        Raises:
            Exception: 协议级异常（实现 bug、断言失败等）允许 raise，
                由调用循环兜底为 ``ToolResult(is_error=True)``。
                **业务级失败应通过 ``ToolResult(is_error=True)`` 表达**，
                不抛业务异常——这样 LLM 能看到失败信号并决定如何处理。
        """
        ...
