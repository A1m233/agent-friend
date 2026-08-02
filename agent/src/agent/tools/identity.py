"""Stable tool identities derived from the builtin capability package layout.

The LLM provider wire protocol still uses each tool's existing flat ``name``.  This
module gives the host a first-class ``namespace/name`` identity without asking every
tool implementation to repeat its package path as a string constant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import Tool

_BUILTIN_NAMESPACE_ROOT = f"{__package__}.builtin"
_NAMESPACE_SEGMENT = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ToolId:
    """Registry-owned identity for one callable tool.

    ``namespace`` is ``None`` for injected/test tools outside the builtin capability
    root.  Builtin namespaces use ``/`` as the canonical separator regardless of the
    host operating system.
    """

    namespace: str | None
    name: str

    @property
    def qualified_name(self) -> str:
        """Return the canonical catalog identity, distinct from the wire call name."""
        return f"{self.namespace}/{self.name}" if self.namespace else self.name


def tool_id_from_implementation(tool: Tool) -> ToolId:
    """Derive a tool identity from its implementation package and declared call name."""
    name = tool.name
    if not isinstance(name, str) or not name or "/" in name:
        raise ValueError(f"工具 name 无效: {name!r}")
    return ToolId(
        namespace=_builtin_namespace_from_module(type(tool).__module__),
        name=name,
    )


def _builtin_namespace_from_module(module_name: str) -> str | None:
    """Return a path relative to :mod:`agent.tools.builtin`, when applicable."""
    prefix = f"{_BUILTIN_NAMESPACE_ROOT}."
    if not module_name.startswith(prefix):
        return None

    relative_parts = module_name[len(prefix) :].split(".")
    # All current builtin implementations use a package-local ``tool.py``.  The
    # namespace represents its containing capability directory, not that leaf file.
    if relative_parts[-1] == "tool":
        relative_parts.pop()
    if not relative_parts:
        raise ValueError(
            f"内置工具必须位于 {_BUILTIN_NAMESPACE_ROOT} 的能力子目录中: {module_name!r}"
        )
    invalid = [part for part in relative_parts if not _NAMESPACE_SEGMENT.fullmatch(part)]
    if invalid:
        raise ValueError(f"工具 namespace 路径包含非法段: {invalid!r}")
    return "/".join(relative_parts)


__all__ = ["ToolId", "tool_id_from_implementation"]
