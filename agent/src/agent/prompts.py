"""Prompt 构建器：决定 :class:`Conversation` 拿到什么 ``system_prompt``。

历史脉络：

- 001 (M0.2): 引入 :class:`MarkdownPromptBuilder`，按 name 加载 ``agent/personas/{name}.md``
- 001 (M0.3): 双层 overlay，user > builtin 同名覆盖
- 003: **重设计** —— name 退化为 user 维度唯一的 slug，**id 是主键**。
  :class:`MarkdownPromptBuilder` 改为持有 ``persona_id``，构建 prompt 时
  委托 :class:`PersonaCatalog.read_content`，**单一真相源**。

frontmatter 解析 / id 寻址 / lazy 补 id 等都在 catalog 一处实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .errors import PersonaNotFoundError
from .personas import PersonaCatalog
from .personas.catalog import DEFAULT_EXTERNAL_DIR as _DEFAULT_EXTERNAL_DIR

DEFAULT_EXTERNAL_PERSONAS_DIR: Path = _DEFAULT_EXTERNAL_DIR
"""默认的用户自定义人设目录（项目根 ``data/personas/``）。

向后兼容名字保留——内部委托给 :data:`agent.personas.catalog.DEFAULT_EXTERNAL_DIR`。
"""


class PromptBuilder(Protocol):
    """Prompt 构建器接口契约。"""

    def build(self) -> str:
        """返回组装好的 system prompt 字符串。"""
        ...


class MarkdownPromptBuilder:
    """003 实现：按 ``persona_id`` 委托 :class:`PersonaCatalog` 读 markdown body。

    Args:
        persona_id: 目标 persona 的 UUID。
        catalog: 可选 :class:`PersonaCatalog` 实例；不传则按 ``external_dir``
            构造一个新的（孵化期每次扫盘 OK）。
        external_dir: 用户自定义 persona 目录（仅当 ``catalog`` 未传时生效）。
            ``None`` 时用 :data:`DEFAULT_EXTERNAL_PERSONAS_DIR`。

    Raises:
        PersonaNotFoundError: 调 :meth:`build` 时 ``persona_id`` 不存在。
    """

    def __init__(
        self,
        persona_id: str,
        *,
        catalog: PersonaCatalog | None = None,
        external_dir: Path | None = None,
    ):
        self.persona_id = persona_id
        self._catalog = catalog or PersonaCatalog(external_dir=external_dir)

    def build(self) -> str:
        """返回 persona 的 prompt 正文（**不含 frontmatter**）。

        Raises:
            PersonaNotFoundError: ``persona_id`` 在 catalog 找不到。
        """
        return self._catalog.read_content(self.persona_id)


__all__ = [
    "DEFAULT_EXTERNAL_PERSONAS_DIR",
    "MarkdownPromptBuilder",
    "PersonaNotFoundError",
    "PromptBuilder",
]
