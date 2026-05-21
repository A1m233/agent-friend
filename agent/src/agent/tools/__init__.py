"""``agent.tools`` —— 工具调用子包。

引擎层一等公民"工具"的完整契约：:class:`Tool` Protocol / :class:`ToolResult` /
:class:`ToolRegistry` / 异常体系。具体内置工具（如 ``WebSearchTool``）落在
:mod:`agent.tools.builtin` 子包下。

详见 docs/requirements/005-engine-tool-calling-and-web-search/。
"""

from .errors import ToolDuplicateError, ToolError, ToolNotFoundError
from .factory import make_default_registry
from .protocol import Tool, ToolResult
from .registry import ToolRegistry

__all__ = [
    "Tool",
    "ToolDuplicateError",
    "ToolError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "make_default_registry",
]
