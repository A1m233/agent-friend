"""``Tool`` Protocol 与 ``ToolResult``：工具调用最小契约。

定义引擎层"工具"的对外协议形态。具体实现可以是内置 Python 类（如
``WebSearchTool``）、未来的 MCP 桥接、远端 RPC 工具等。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.1.1 / §4.1.2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

from ..attachments import AttachmentRef
from ..attachments.models import validate_image_refs

IMAGE_INPUT_SUPPORTED_ARG = "__agent_friend_tool_images_supported"


@dataclass(frozen=True)
class ToolResult:
    """工具执行的统一返回结构。

    Attributes:
        text: 喂回 LLM 的纯文本（即使 tool 内部产生结构化数据，也要在这里
            序列化成文本）。图像通过 images 独立传递，由 provider 投影内容块。
        is_error: ``True`` 表示业务级失败（网络错 / key 错 / 限流等）；
            ``False`` 表示正常结果。失败时 ``text`` 仍要给出失败描述，
            交由 LLM 决定是否再试或换路。
        meta: 供观测 / 日志用的额外信息（如耗时、result 数量、原始 provider
            错误码）。**不会喂回 LLM**。CLI 可以读它做更精细的可视化。
    """

    text: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    images: tuple[AttachmentRef, ...] = ()

    def __post_init__(self) -> None:
        validate_image_refs(self.images)
        if self.is_error and self.images:
            raise ValueError("failed tool results cannot contain images")


@runtime_checkable
class Tool(Protocol):
    """单个工具的最小契约。

    Attributes:
        name: 工具名（在同一个 :class:`ToolRegistry` 内唯一；建议 snake_case）。
        description: 给 LLM 看的工具使用说明。它不应只是产品名或一句话摘要；
            应写清该工具做什么、何时必须用、何时不要用、参数来源 / 证据边界、
            失败或空结果时如何继续，以及是否需要用户确认。
        parameters_schema: JSON Schema (draft-07) 描述 :meth:`invoke` 入参；
            遵循 OpenAI tool calling spec 中 ``function.parameters`` 的格式。
            :class:`ToolRegistry` 在调用前会对剔除宿主隐藏字段后的模型参数执行同一
            schema 校验；工具实现仍负责权限、路径、所有权和业务不变量检查。

    Note:
        Tool 实现不手写 namespace。注册时 :class:`ToolRegistry` 根据实现类相对于
        ``agent.tools.builtin`` 的 package 路径自动生成一等 ``ToolId``，并把当前
        registry 的可信 capability catalog 投影给模型；provider wire name 仍使用
        ``name``，保持现有函数调用兼容。

        本期同步接口（不是 ``async``），与 0002 §3.4 选定的"同步优先"基调一致。
        未来若引入异步工具（如长任务、流式日志输出），通过新增可选方法
        ``async_invoke`` 扩展，**不破坏现有同步实现**。

        Protocol 上**不绑定 UI 渲染**（无 ``render_xxx`` / ``user_facing_name``
        等方法）——UI 渲染统一由 CLI / 前端基于 ``ConversationEvent`` 决定。
        Protocol 本身**不绑定权限策略**；需要权限的工具可通过宿主注入的隐藏运行时
        上下文执行校验，不能把模型参数当成授权事实。

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
            args: 经 :class:`ToolRegistry` 按 ``parameters_schema`` 校验、并附加了必要
                宿主隐藏上下文的入参字典。直接单测实现时仍应传合法 public 参数；
                工具不得把 schema validation 当作权限或副作用校验的替代品。

        Returns:
            统一的 :class:`ToolResult`。

        Raises:
            Exception: 协议级异常（实现 bug、断言失败等）允许 raise，
                由调用循环兜底为 ``ToolResult(is_error=True)``。
                **业务级失败应通过 ``ToolResult(is_error=True)`` 表达**，
                不抛业务异常——这样 LLM 能看到失败信号并决定如何处理。
        """
        ...
