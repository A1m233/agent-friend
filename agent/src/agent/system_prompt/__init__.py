"""``agent.system_prompt`` 子包：按职责解耦的 system_prompt 组合器。

详见 docs/requirements/004-engine-system-prompt-composer/design.md
和 005 design §4.10（``RuntimeContextSection``）。

公开 API：

- :class:`Section`：单个槽位的协议（``key`` + ``render()``）
- :class:`StaticSection`：持有固定文本的默认实现
- :class:`PersonaSection`：动态从 :class:`PersonaCatalog` 读 persona body 的实现
- :class:`RuntimeContextSection`：动态注入运行时上下文（005 起）
- :class:`ChannelSection`：按 ``session.current_channel`` 注入通道指令（007 起）
- :class:`SystemPromptComposer`：有序的若干 Section 的不可变装配；
  ``compose() -> str`` 输出最终 system_prompt
"""

from __future__ import annotations

from .composer import Section, SystemPromptComposer
from .sections import (
    ChannelSection,
    PersonaSection,
    RuntimeContextSection,
    StaticSection,
)

__all__ = [
    "ChannelSection",
    "PersonaSection",
    "RuntimeContextSection",
    "Section",
    "StaticSection",
    "SystemPromptComposer",
]
