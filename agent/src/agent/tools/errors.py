"""``agent.tools`` 子包专用异常。

跟 :class:`agent.errors.AgentError` 的设计一致：上层调用方（CLI / 未来 API）
``catch`` 这些项目级异常做对外提示，**不直接暴露 Python 内置异常**。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.1。
"""

from __future__ import annotations

from ..errors import AgentError


class ToolError(AgentError):
    """所有 ``agent.tools`` 模块异常的基类。"""


class ToolNotFoundError(ToolError):
    """指定 name 在当前 :class:`ToolRegistry` 中找不到。

    典型场景：LLM 返回的 ``tool_call.name`` 拼写错误 / 调用了未注册的工具。
    调用循环兜底为 :class:`ToolResult` (``is_error=True``) 喂回 LLM，
    让 LLM 自我修正后续调用。
    """


class ToolDuplicateError(ToolError):
    """构造 :class:`ToolRegistry` 时发现重名工具。

    fail-fast：避免运行期"调到的不是预期的 tool"这种隐蔽 bug。
    """
