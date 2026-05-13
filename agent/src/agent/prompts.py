"""Prompt 构建器：决定 ``Conversation`` 拿到什么 ``system_prompt``。

阶段 1 提供 :class:`MarkdownPromptBuilder`，从 ``agent/personas/{name}.md`` 读取
markdown 文件作为 system prompt。**人设的语义内容（性格、行为准则等）阶段 1
不在代码里硬编码**——只搭机制不填内容，让用户在 markdown 里自由表达。

阶段 2 / M0.3 可加 ``data/personas/`` overlay（用户自定义优先于默认）、
动态拼接（时间感知、用户信息、关系状态等）。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.4。
"""

from __future__ import annotations

from importlib.resources import files
from typing import Protocol


class PromptBuilder(Protocol):
    """Prompt 构建器接口契约。"""

    def build(self) -> str:
        """返回组装好的 system prompt 字符串。"""
        ...


class MarkdownPromptBuilder:
    """阶段 1 实现：从 ``agent/personas/{name}.md`` 直接读取 markdown 文件。

    Args:
        persona_name: 人设名称，对应 ``agent/personas/{persona_name}.md``。
            默认 ``"default"``。

    Raises:
        FileNotFoundError: 在 :meth:`build` 时找不到对应的 markdown 文件。
    """

    def __init__(self, persona_name: str = "default"):
        self.persona_name = persona_name

    def build(self) -> str:
        resource = files("agent.personas").joinpath(f"{self.persona_name}.md")
        return resource.read_text(encoding="utf-8").strip()
