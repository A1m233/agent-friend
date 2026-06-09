"""``agent.personas`` 子包：persona 资源 + 引擎层 CRUD 入口。

001 时本子包只放 ``*.md`` 资源（用 ``importlib.resources`` 加载）。
003 起升级为代码+数据混合包，对外暴露：

- :class:`PersonaCatalog`：list / get / read_content / find_by_name / create /
  update / delete / rename 全套 CRUD
- :class:`PersonaInfo`：persona 元信息载体
- :data:`KEEP`：``update`` 三态语义的"保持"哨兵
- :data:`BUILTIN_DEFAULT_PERSONA_ID`：内置 default persona 的稳定 UUID

详见 README.md 与 docs/requirements/003-engine-persona-management/。
"""

from __future__ import annotations

from . import frontmatter
from .catalog import (
    KEEP,
    PersonaCatalog,
    PersonaInfo,
)
from .identity import BUILTIN_DEFAULT_PERSONA_ID

__all__ = [
    "BUILTIN_DEFAULT_PERSONA_ID",
    "KEEP",
    "PersonaCatalog",
    "PersonaInfo",
    "frontmatter",
]
