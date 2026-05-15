"""``Section`` 默认实现：:class:`StaticSection` / :class:`PersonaSection`。

详见 docs/requirements/004-engine-system-prompt-composer/design.md §4.2。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..personas import PersonaCatalog


@dataclass(frozen=True)
class StaticSection:
    """持有固定文本的 section。

    Args:
        key: 槽位标识（与 :class:`SystemPromptComposer` 装配 API 配合）。
        text: 渲染时返回的文本。空字符串 / 仅空白时 :meth:`render` 返回 ``None``
            （视为该轮无内容）。
    """

    key: str
    text: str

    def render(self) -> str | None:
        """返回去除首尾空白后的文本；为空时返回 ``None``。"""
        stripped = self.text.strip()
        return stripped if stripped else None


@dataclass(frozen=True)
class PersonaSection:
    """动态从 :class:`PersonaCatalog` 读 persona body 的 section。

    Args:
        key: 槽位标识，约定为 ``"persona"``。
        persona_id: 目标 persona 的 UUID。
        catalog: persona 真相源；render 时调 ``catalog.read_content(persona_id)``。

    Raises:
        PersonaNotFoundError: :meth:`render` 时 ``persona_id`` 在 catalog
            找不到（与 003 ``MarkdownPromptBuilder.build`` 行为一致）。
    """

    key: str
    persona_id: str
    catalog: PersonaCatalog

    def render(self) -> str | None:
        """读 persona body；空内容时返回 ``None``。"""
        text = self.catalog.read_content(self.persona_id)
        stripped = text.strip()
        return stripped if stripped else None
